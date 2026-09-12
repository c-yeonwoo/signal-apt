from realty_signal.audit_independence import summarize_calendar_cohorts


def test_calendar_cohorts_collapse_same_week_region_rows():
    summary = summarize_calendar_cohorts([
        ("2020-01-03", "강남구"),
        ("2020-01-03", "송파구"),
        ("2020-01-10", "노원구"),
        ("2020-01-10", "노원구"),  # 같은 지역 중복은 한 온셋으로만 센다
    ])

    assert summary["raw_onsets"] == 3
    assert summary["calendar_cohorts"] == 2
    assert summary["median_rows_per_cohort"] == 1.5
    assert summary["largest_cohort_rows"] == 2
    assert summary["cohorts_by_year"] == {"2020": 2}
