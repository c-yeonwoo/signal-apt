"""CLI — KB 주간 시계열 엑셀에서 지역별 매수·매도 시그널 리포트/대시보드.

사용:
  signal report <엑셀경로>                 # 시그널 요약 테이블
  signal report <엑셀경로> --region 서울    # 특정 지역 시계열 추이
  signal report <엑셀경로> --only STRONG_BUY,BUY
  signal build  <엑셀경로>                 # parquet 캐시 생성(대시보드용)
  signal serve                            # 대시보드 웹서버 실행
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from realty_signal import store
from realty_signal.ingest import kb_weekly
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
def serve(host: str = "127.0.0.1", port: int = 8765):
    """대시보드 웹서버 실행 (먼저 `signal build` 필요)."""
    import uvicorn

    if not store.CACHE_FILE.exists():
        console.print("[red]캐시 없음.[/red] 먼저 `signal build <xlsx>` 실행하세요.")
        raise typer.Exit(1)
    console.print(f"[green]대시보드:[/green] http://{host}:{port}")
    uvicorn.run("realty_signal.api:app", host=host, port=port, log_level="warning")


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
