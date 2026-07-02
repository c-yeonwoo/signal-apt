"""CLI — KB 주간 시계열 엑셀에서 지역별 매수·매도 시그널 리포트/대시보드.

사용:
  signal report <엑셀경로>                 # 시그널 요약 테이블
  signal report <엑셀경로> --region 서울    # 특정 지역 시계열 추이
  signal report <엑셀경로> --only STRONG_BUY,BUY
  signal build  <엑셀경로>                 # parquet 캐시 생성(대시보드용)
  signal serve                            # 대시보드 웹서버 실행
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from realty_signal import store
from realty_signal.ingest import kb_weekly
from realty_signal.signals import history
from realty_signal.signals.engine import SignalConfig, evaluate

app = typer.Typer(add_completion=False, help="KB 주간 시계열 아파트 시그널 분석")
console = Console()

_COLOR = {
    "STRONG_BUY": "bold green",
    "BUY": "green",
    "WATCH": "yellow",
    "NEUTRAL": "white",
    "SELL_RISK": "bold red",
}


@app.command()
def build(path: Path = typer.Argument(..., exists=True, help="KB 주간 시계열 .xlsx")):
    """엑셀을 파싱해 parquet 캐시 생성 (대시보드/반복조회용)."""
    console.print(f"[dim]파싱: {path.name}[/dim]")
    kb = store.build(path)
    console.print(f"[green]캐시 저장 완료[/green] → {store.CACHE_FILE} "
                  f"(지역 {len(kb.regions)} · 최신 {kb.last_date.date()})")


@app.command()
def fetch():
    """KB 데이터허브에서 최신 지표를 자동 수집해 캐시 갱신 (엑셀 불필요)."""
    console.print("[dim]KB 데이터허브 수집 중…[/dim]")
    kb = store.fetch()
    console.print(f"[green]수집 완료[/green] → {store.CACHE_FILE} "
                  f"(지역 {len(kb.regions)} · 지표 {len(kb.metrics)} · 최신 {kb.last_date.date()})")


def _macos_notify(title: str, message: str):
    """macOS 알림 센터로 푸시 (실패해도 무시)."""
    import shutil
    import subprocess

    osa = shutil.which("osascript")
    if not osa:
        return
    safe = message.replace('"', "'")[:240]
    subprocess.run(
        [osa, "-e", f'display notification "{safe}" with title "{title}"'],
        capture_output=True,
    )


@app.command()
def watch(
    quiet: bool = typer.Option(False, help="변화 없으면 출력 생략(cron용)"),
    notify: bool = typer.Option(False, help="변화 시 macOS 알림 센터로 푸시"),
):
    """최신 지표 수집 → 지난주 대비 등급 변화 지역 알림 + 스냅샷 갱신."""
    kb = store.fetch()
    df = evaluate(kb, SignalConfig(), store.load_supply())
    as_of = str(kb.last_date.date())
    prev = history.load_snapshot()
    changes = history.diff(prev, df)

    if prev.get("as_of") == as_of and not changes:
        if not quiet:
            console.print(f"[dim]변화 없음 (데이터 {as_of}, 이전과 동일)[/dim]")
        return
    if not changes:
        history.save_snapshot(df, as_of)
        if not quiet:
            console.print(f"[dim]등급 변화 없음 (데이터 {as_of})[/dim]")
        return

    table = Table(title=f"시그널 변화 ({as_of}, 지난주 대비)", header_style="bold")
    for col in ["지역", "방향", "이전→현재", "근거"]:
        table.add_column(col, overflow="fold")
    for ch in changes:
        color = "green" if ch["delta"] > 0 and ch["new"] != "SELL_RISK" else "red"
        table.add_row(ch["region"], f"[{color}]{ch['direction']}[/{color}]",
                      f"{ch['old']} → {ch['new']}", ch["근거"])
    console.print(table)
    console.print(f"\n[bold]{len(changes)}개 지역 등급 변화[/bold]")
    if notify:
        top = ", ".join(f"{c['region']} {c['old']}→{c['new']}" for c in changes[:3])
        _macos_notify(f"부동산 시그널 변화 {len(changes)}건 ({as_of})", top)
    history.save_snapshot(df, as_of)
    _warm_favorites_quiet(quiet)


def _warm_favorites_quiet(quiet: bool) -> None:
    """관심단지 실거래 캐시 주간 워밍 — 사용자 콜드스타트 제거. best-effort."""
    try:
        from realty_signal.api import warm_favorite_complexes
        r = warm_favorite_complexes()
        if not quiet:
            console.print(f"[dim]관심단지 워밍: 신규 {r.get('warmed',0)} · 스킵 {r.get('skipped',0)}[/dim]")
    except Exception as e:  # noqa: BLE001
        if not quiet:
            console.print(f"[dim]관심단지 워밍 생략: {e}[/dim]")


@app.command()
def localities():
    """수도권 시군구 저평가 분석 데이터 수집 (국토부·ODsay·상가·OSM, 수 분 소요)."""
    console.print("[dim]수도권 시군구 입지·가격 수집 중… (수 분)[/dim]")
    df = store.build_localities()
    console.print(f"[green]완료[/green] → {store.LOCALITY_FILE} ({len(df)}개 지역)")
    if len(df):
        top = df.nlargest(5, "저평가도")[["region", "입지점수", "price", "저평가도"]]
        console.print(top.to_string(index=False))


@app.command()
def volumes():
    """시군구 월별 거래량 수집 (국토부, 수 분 소요)."""
    console.print("[dim]시군구 거래량 수집 중…[/dim]")
    v = store.build_volumes()
    console.print(f"[green]완료[/green] → {store.VOLUME_FILE} ({len(v)}개 지역)")


@app.command()
def serve(
    host: str = typer.Option(lambda: os.environ.get("HOST", "127.0.0.1")),
    port: int = typer.Option(lambda: int(os.environ.get("PORT", "8765"))),
):
    """대시보드 웹서버 실행. 캐시 없으면 기동 시 자동 수집(lifespan)."""
    import uvicorn

    console.print(f"[green]대시보드:[/green] http://{host}:{port}")
    uvicorn.run("realty_signal.api:app", host=host, port=port, log_level="warning")


@app.command()
def backup():
    """app.db 를 S3 호환 스토리지로 백업 (env 설정 시). 수동/cron 실행용."""
    from realty_signal import backup as bk
    if not bk.enabled():
        console.print("[yellow]백업 env 미설정[/yellow] (BACKUP_S3_BUCKET/KEY_ID/SECRET)")
        raise typer.Exit(1)
    key = bk.run_backup()
    console.print(f"[green]백업 완료:[/green] {key}" if key else "[red]백업 실패[/red]")


@app.command()
def report(
    path: Path = typer.Argument(..., exists=True, help="KB 주간 시계열 .xlsx 경로"),
    region: str = typer.Option(None, help="특정 지역의 최근 추이만 보기"),
    only: str = typer.Option(None, help="표시할 시그널 (쉼표구분: STRONG_BUY,BUY,...)"),
    weeks: int = typer.Option(4, help="모멘텀 산정 주 수"),
):
    """시그널 리포트 출력."""
    console.print(f"[dim]로딩: {path.name}[/dim]")
    kb = kb_weekly.load(path)
    console.print(
        f"[dim]지역 {len(kb.regions)}개 · 지표 {len(kb.metrics)}종 · 최신 {kb.last_date.date()}[/dim]\n"
    )

    if region:
        _region_trend(kb, region, weeks)
        return

    df = evaluate(kb, SignalConfig(momentum_weeks=weeks))
    if only:
        keep = {s.strip().upper() for s in only.split(",")}
        df = df[df["signal"].isin(keep)]

    table = Table(title=f"지역별 시그널 (최신 {kb.last_date.date()})", header_style="bold")
    for col in df.columns:
        table.add_column(col, overflow="fold")
    for _, r in df.iterrows():
        style = _COLOR.get(r["signal"], "white")
        table.add_row(*[f"[{style}]{r['signal']}[/{style}]" if c == "signal"
                        else ("" if r[c] is None else str(r[c])) for c in df.columns])
    console.print(table)

    counts = df["signal"].value_counts()
    summary = "  ".join(f"[{_COLOR.get(k,'white')}]{k}={v}[/]" for k, v in counts.items())
    console.print(f"\n{summary}")


def _region_trend(kb, region: str, weeks: int):
    if region not in kb.regions:
        cand = [r for r in kb.regions if region in r]
        console.print(f"[red]'{region}' 없음.[/red] 유사: {', '.join(cand[:10]) or '없음'}")
        raise typer.Exit(1)

    table = Table(title=f"{region} — 최근 {weeks}주", header_style="bold")
    table.add_column("주")
    metrics = [m for m in ["jeonse_supply", "buyer_demand", "buyer_superiority",
                           "sale_change", "jeonse_change"] if m in kb.metrics]
    label = {"jeonse_supply": "전세수급", "buyer_demand": "매수세우위",
             "buyer_superiority": "매수우위지수", "sale_change": "매매증감%",
             "jeonse_change": "전세증감%"}
    for m in metrics:
        table.add_column(label[m])

    series = {m: kb.series(region, m) for m in metrics}
    dates = sorted(set().union(*[s.tail(weeks).index for s in series.values()]))
    for d in dates:
        cells = [str(d.date())]
        for m in metrics:
            v = series[m].get(d)
            cells.append("" if v is None else f"{v:.2f}")
        table.add_row(*cells)
    console.print(table)


if __name__ == "__main__":
    app()
