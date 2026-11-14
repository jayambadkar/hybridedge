# Equal Care: the cancer pathway by county

`/pathway/` is a separate app on the same agent runtime. It answers the three questions of the
AstraZeneca "Equal Care Across Finland" brief for each of the 23 wellbeing services counties:

1. **Is there a gap?** County value against Finland, the best county and the national target.
2. **Where in the pathway?** Early detection → primary care → referral → treatment start → outcome.
3. **What should be done?** Proposals with an owner, steps, a 90-day start, a KPI and the evidence.

## Data

Public, aggregate data only. `scripts/build_pathway_data.py` builds `hybridedge/pathway_data.json`
from Sotkanet (THL's indicator bank) and Statistics Finland's county boundaries. The app does not
call these services at run time.

| Stage | Indicator (Sotkanet id) | Source |
|---|---|---|
| Early detection | Screening participation: cervical (3620), breast (3621), bowel (6103) | Finnish Cancer Registry |
| Primary care | Non-urgent doctor visits that waited over 7 days (6411) | THL, Avohilmo |
| Referral | Referrals processed within 3 days (13); waiting over 6 months for specialist care (3336) | THL |
| Treatment start | Not published by county | – |
| Outcome (Goal 3) | Five-year survival: bowel (3627), cervical (3626), breast (3625) | Finnish Cancer Registry |

Targets and measures come from the National Cancer Strategy 2026–2035 and its implementation
roadmap. Each proposal cites the roadmap measure it implements and the study behind it. All
sources, the method and the limits of the data are listed in the app under **Sources and limits**.

## Method

- The latest year reported by at least 15 counties is used for each indicator.
- A county's distance from the others is (value − mean of counties) ÷ standard deviation. A stage's
  score is the mean of its indicators. A stage is named as the county's gap when it is the worst of
  the three measured care stages and at least 0.5 standard deviations worse than the average county.
- People counts are arithmetic on published counts (invited = attended ÷ participation rate).
- The findings and proposals are computed, not generated. The agent team only writes the brief, and
  every figure in the brief is checked against the data (`POST /api/pathway/verify`).

## Limits

- Time from referral or diagnosis to first treatment is not published by county. The app shows this
  stage as not measured and proposes how a county can start measuring it.
- Primary care and referral figures cover all patients, not only suspected cancer.
- Survival has no published confidence intervals and reflects ten diagnosis years. Small counties vary.
- The comparisons show differences, not causes.
