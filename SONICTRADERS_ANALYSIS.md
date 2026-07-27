# Sonictraders (@sonictraders) — Channel Structure Analysis + Method Test

*Pulled 2026-07-27 from the public web preview (t.me/s/sonictraders).
No Telegram API session used — public preview only (~20 recent posts).*

## Channel facts

- **TradeWithSonic** — 5.78K subscribers, Singapore team, bilingual EN/Myanmar
- 9.78K photos, 838 videos, 8.86K links (media-heavy, long-running)
- Daily live stream: "London Session — Order Flow & Volume Profile", 3PM SGT
  (= 08:00 London / 07:00 UTC), covering NQ, gold, silver, oil, forex, crypto
- Monetization: Bookmap affiliate ("How do I make $$ consistently?? Bookmap +
  Level 2 data"), referrals to other signal channels, paid streams implied

## Content mix (visible sample)

| type | share | examples |
|---|---|---|
| Geopolitical/macro news flashes | ~40% | Iran/Saudi/Korea/BOJ items, "crd Internet/Megatron" |
| Education (bilingual) | ~15% | "the close is the verdict" candle lesson |
| Market calls with levels | ~10% | Gold WK31: support $3,982, bullish only above $4,100–4,120 |
| Stream promotion | ~15% | daily London session YouTube link |
| Affiliate/referral | ~10% | Bookmap, @cryexc_alerts |
| Track-record claims | ~5% | "4 Trades 4 Wins" — self-reported, no audited record |
| Noise | ~5% | Singapore local news |

## What is actually learnable (and what is not)

**Keep (process, matches our doctrine):**
1. **Session specialization** — he trades ONE window, the London open. That is
   exactly the 08:00 UTC block in our measured kill-zone mask (+18.6bp KZ set).
   Liquidity clusters at session opens; his instinct and our measurement agree.
2. **Invalidation-first calls** — "bullish ONLY above $4,100–4,120" is level +
   condition + invalidation. Our tickets do the same structurally (SL1/SL2).

**Not learnable / not verifiable:**
3. His claimed real edge — Bookmap order-flow / iceberg reading — lives in paid
   streams, is discretionary, and has no published audited record. "4 wins"
   posts are marketing, not sample size.
4. Full history pull requires Telegram API credentials (Telethon) — not used.

## His one TESTABLE claim — measured, and it fails INVERTED

Claim: shooting star + confirmation candle closing in the **bottom third** of
the star's range = valid short; middle-third close = pause ("shorts get run
over"). Tested on our panel: 20 USDT perps, 1h bars, ~11 months,
**19,018 events** (`sonic_h1_test.py`, results in `sonic_h1_test.json`):

| bucket | n | 4h mean | 4h med | t | 8h mean | 8h med |
|---|---|---|---|---|---|---|
| bottom third (his VALID short) | 4,105 | **+8.88bp UP** | 0.00 | **2.10** | +7.79 | −7.00 |
| middle third (his "pause") | 3,727 | −1.24 | −8.20 | −0.27 | −11.80 | −15.63 |
| top third | 2,484 | −5.03 | −9.83 | −1.05 | −6.02 | −12.62 |

Contrast bottom−middle at 4h: **+10.1bp — opposite sign to his claim.**
On crypto 1h, bottom-third confirmation closes mark *exhaustion* (trapped
sellers → squeeze UP, t=2.1), not continuation DOWN. And even the inverted
edge (+8.9bp mean, median 0) doesn't clear one-way taker cost — untradeable.

**Verdict: his public testable rule is folklore on crypto 1h (sign-flipped).
Adopt his session discipline and invalidation framing; ignore the candle rule.
Do not import anything that wasn't measured — the doctrine holds for gurus too.**
