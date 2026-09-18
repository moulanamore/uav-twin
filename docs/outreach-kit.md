---
title: "UAV Twin — Operator Outreach Kit"
subtitle: "Templates, contacts, and follow-up cadence for Phase 2 conversations"
author: "Asick Jahir · with Claude (Anthropic)"
date: "September 2026"
geometry: margin=2.2cm
fontsize: 11pt
linkcolor: RoyalBlue
urlcolor: RoyalBlue
---

# 1. What we're actually asking for

Not a sale. Not a pilot. A **20-minute conversation to validate the
positioning**. Specifically:

- Do you actually feel the pain we think you feel? (Excel records
  falling apart, batteries retired too early, motor bearing failures
  discovered only in flight.)
- What would you pay for a solution that ran on your own infrastructure
  instead of a per-vehicle SaaS subscription?
- Would you consider being a design partner — sharing anonymised flight
  logs in exchange for a free tier and direct input into the roadmap?

The tool exists, the twin works, the models trained. All we need now is
one real operator's voice to steer Phase 4.

# 2. Email templates

Rewrite in your own voice. These are drafts, not scripts. Keep every
send under 150 words. Attach nothing on the first email — link to the
demo and the GitHub repo, that's the entire pitch.

## Template A — Founder / CEO / CTO of a mid-size drone company

**Best for**: Agnishwar Jayaprakash (Garuda), Prem Kumar Vislawath
(Marut), Mrinal Pai (Skylark), Vipul Singh (Aereo), Neel Mehta / Nihar
Vartak (Asteria).

**Subject line variants** (pick one, A/B them if you send more than 5):

- `Open-source battery RUL twin for [Company] — 20 min?`
- `Aeronautical engineer in Chennai — quick question about your fleet`
- `Built an open ArduPilot fleet-health twin — feedback?`

**Body**:

> Hi [First name],
>
> I'm an aeronautical engineer in Chennai (M.Sc. propulsion, Politecnico
> di Milano) and I've been building an open-source digital twin for
> ArduPilot / PX4 multirotor fleets. It fuses OpenModelica physics with
> a trained XGBoost bearing-wear classifier and a Gaussian-process
> battery-RUL predictor — all on your own infra, no SaaS lock-in.
>
> Live demo (synthesised data, no clone needed):
> https://moulanamore.github.io/uav-twin/
>
> Repo: https://github.com/moulanamore/uav-twin
>
> I'm not trying to sell you anything — I'm looking for one real
> operator to validate whether the positioning holds. Specifically: is
> per-flight battery-life and bearing-wear prediction actually valuable
> to [Company]'s ops at fleet scale, or is a simpler Airdata-style
> dashboard enough?
>
> Would you have 20 minutes in the next two weeks? Any time between 5pm
> and 7pm IST works on my side.
>
> Thanks,
> Asick
> https://github.com/moulanamore

## Template B — Head of ops / fleet manager / MRO lead

**Best for**: someone whose title has "operations", "maintenance",
"fleet" in it, at Tier 2+ companies (Skylark, Aereo, Asteria).

**Subject**:

- `Battery replacement cadence — quick question`

**Body**:

> Hi [First name],
>
> I run into a lot of drone-operator posts asking how to track battery
> health without paying an Airdata subscription per flight. I've been
> building an open, self-hostable answer to that — a physics + ML twin
> that runs on any ArduPilot/PX4 fleet.
>
> Two quick questions for you, if you have a minute:
>
> 1. How do you decide when to retire a battery today — cycle count,
>    voltage sag, calendar, or the pilot's judgement?
> 2. What would change for [Company] if you had a per-battery RUL
>    forecast with a 90% credible interval, updated every flight?
>
> Live demo: https://moulanamore.github.io/uav-twin/
> Source: https://github.com/moulanamore/uav-twin
>
> Happy to jump on a 15-minute call if either question is interesting.
>
> Thanks,
> Asick (Chennai)

## Template C — Technical DM on LinkedIn

**Best for**: LinkedIn cold outreach where the character limit and
attention span are both shorter than email. Send from your own account,
not from a page.

> Hi [First name] — aeronautical engineer in Chennai here. Built an
> open-source ArduPilot/PX4 fleet twin (physics + XGBoost bearing wear +
> GP battery RUL). Live demo:
> moulanamore.github.io/uav-twin. Would 20 minutes to validate the
> positioning be possible? Not selling — looking for a design partner
> to shape Phase 4.

If they engage, drop the repo link in the next message.

# 3. Contact list

Tier 1 first (agri-spray, high battery-cycling pain). Send no more than
3 outreach emails per day so you can respond thoughtfully to each reply.

## Tier 1 — Agri-spray, high fleet count

| Company | Person | Role | LinkedIn | Notes |
|---|---|---|---|---|
| **Garuda Aerospace** | Agnishwar Jayaprakash | Founder & CEO | [linkedin.com/in/agnishwarjayaprakash](https://in.linkedin.com/in/agnishwarjayaprakash) | Chennai-based (proximity advantage). 2,000+ agri-drones, 10L flight hours in past year. Highest priority. |
| **Marut Drones** | Prem Kumar Vislawath | Founder & CEO | Search LinkedIn for "Prem Kumar Vislawath Marut Drones" | Hyderabad. $6.2M Series A. Multi-utility agri drones. |

## Tier 2 — Enterprise inspection, mapping, survey

| Company | Person | Role | LinkedIn | Notes |
|---|---|---|---|---|
| **Skylark Drones** | Mrinal Pai | Co-Founder | [linkedin.com/in/mrinalpai](https://in.linkedin.com/in/mrinalpai) | Bangalore. Industrial inspection, mapping. ArduPilot-based custom builds likely. |
| **Aereo** (formerly Aarav Unmanned) | Vipul Singh | Co-Founder | Search LinkedIn "Vipul Singh Aereo" | Bangalore. Mining, urban planning, infrastructure. Large fleet. |
| **Asteria Aerospace** | Neel Mehta | Director & Co-Founder | [linkedin.com/in/neel-mehta-61006031](https://in.linkedin.com/in/neel-mehta-61006031) | Bangalore. Reliance subsidiary. Defence-adjacent commercial services. Slower to close but bigger. |
| **Asteria Aerospace** | Nihar Vartak | Co-Founder | [linkedin.com/in/nihar-vartak-b0663b9](https://in.linkedin.com/in/nihar-vartak-b0663b9) | Alternative contact if Neel doesn't respond. |

## Tier 3 — Adjacent (later)

| Company | Focus | Notes |
|---|---|---|
| **Redwing Labs** | Autonomous medical delivery | Small fleet, tight margins — could care about battery life a lot. |
| **DGCA-registered agri operators** | Smaller local operators | 5,000+ registered. Hit them via LinkedIn hashtag scraping later. |

# 4. Follow-up cadence

Day 0: Send the email.
Day 4: If no reply, one soft nudge — "reposting in case this got buried,
worth 20 min?" Two-sentence max.
Day 12: If still no reply, connect on LinkedIn with the template-C DM.
Day 30: Move on. Don't chase further than that — it reads as
desperation and closes the door for the next reason to reach out
(Phase 4 launch, a paper, a press mention).

Track responses in a simple spreadsheet: one row per person, columns
for date sent / date replied / meeting scheduled / notes. Don't over-
engineer this; a Google Sheet with 20 rows is enough.

# 5. What to run in the 20-minute meeting

Once someone accepts, protect their time. Sample agenda:

**Minute 0–2**: You introduce yourself and the twin in 90 seconds. Show
the live demo screen-shared. Don't over-explain — the visual does the
work.

**Minute 2–10**: Ask, don't tell. Prepared questions:

1. "How does [Company] currently decide when a battery is retired?"
2. "When a motor fails in flight, what's the first indicator you catch?"
3. "How are your maintenance records structured today — Excel, custom
   internal tool, a SaaS product?"
4. "If a per-vehicle RUL forecast with a 90% credible interval landed
   on the pilot's tablet before every flight, would that change how
   you dispatch?"

**Minute 10–15**: Their questions about the twin. Be honest about
limitations:

- Models trained on synthetic ALFA/NASA data — needs real-fleet
  retraining to generalise to their duty cycles
- The FMU physics is Modelica-generated but the parameters are
  literature defaults — bench-testing their motors would refine them
- Open source; no support SLA today

**Minute 15–20**: The one specific ask.

"If we agree the twin looks useful, would [Company] consider being a
design partner for the next three months? Concretely: share anonymised
log files from 20-30 flights in exchange for free tier access, direct
input into which features we build next, and named credit in the
project when we publish."

If they say maybe: send a one-page design-partner memo within 24
hours. If they say no: ask what would make them say yes.

# 6. Parallel channels

Not every conversation starts with a cold email. Where each helps:

- **ArduPilot Discourse** ([discuss.ardupilot.org](https://discuss.ardupilot.org/))
  — post a short project introduction in the "Show and Tell" or "Ground
  Control Software" category. Operators lurk here.
- **DGCA drone stakeholder meets** — attend one if any are announced.
  Chennai / Bangalore see 2-3 a year.
- **LinkedIn posts** — one every 2 weeks with a specific technical
  finding from the twin (e.g., "here's why we chose Matérn 5/2 for
  battery RUL instead of an LSTM"). Builds credibility without asking
  for anything.
- **Aerospace propulsion friends from Politecnico** — introduce the
  twin to any classmates now working in propulsion at Airbus / Leonardo
  / Marelli. Not customers, but potential validators of the physics.

# 7. What "success" looks like at each stage

- **Week 1 of outreach**: 10 emails sent, 2 opens (LinkedIn stalking
  confirms), 0 replies. This is normal. Keep going.
- **Week 3**: First real reply, either a "not now, but interesting"
  or a "sure, book a call". Either is signal.
- **Week 6**: First 20-minute call held. Write up notes within 24 hours.
- **Week 8**: Second call held. Compare notes — is the same pain
  showing up across both?
- **Month 3**: One design-partner agreement in principle, even if
  informal. That unlocks Phase 4 data and validates every assumption
  in the market analysis.

If no design partner materialises by Month 6, revisit the positioning.
Options at that point: pivot to a specific vertical (only agri-spray?),
pivot to a specific persona (only maintenance managers?), or pivot to
a specific business model (services / consulting instead of
product).

---

*This is a template. Track what actually works in your responses column
and evolve the copy as evidence arrives. The rule of thumb: your third
email will convert 3x better than your first.*
