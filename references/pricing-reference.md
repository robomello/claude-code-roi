# Pricing Reference for Cost Estimates

Current costs and benchmarks for Roberto's infrastructure. Update this when prices change.

## Claude Plans (as of 2026-03)

| Plan | Cost | Tokens/Context | Notes |
|------|------|----------------|-------|
| Claude Max | $200/mo | Unlimited, 1M context | Roberto's current plan |
| Claude Pro | $20/mo | Limited usage | Not used |
| Claude API (Opus) | ~$15/$75 per 1M tok in/out | Pay-per-use | NOT used (CLI only) |
| Claude API (Sonnet) | ~$3/$15 per 1M tok in/out | Pay-per-use | NOT used |
| Claude API (Haiku) | ~$0.25/$1.25 per 1M tok in/out | Pay-per-use | NOT used |

## Infrastructure Costs

| Service | Cost | Notes |
|---------|------|-------|
| Cloudflare Tunnel | Free | 18+ subdomains |
| Synology NAS | One-time | DS923+ already owned |
| Electricity (server) | ~$30-50/mo | Estimated for 24/7 operation |
| Domain (synai.ai) | ~$15/yr | Cloudflare registrar |
| Domain (evolutionlabs.blog) | ~$12/yr | |
| Domain (drinkwaretrove.com) | ~$12/yr | |

## Etsy Costs

| Item | Cost | Notes |
|------|------|-------|
| Listing fees | $0.20/listing | Per 4-month listing period |
| Transaction fee | 6.5% | On sale price + shipping |
| Etsy Plus | $10/mo | Per shop |
| Alura subscription | ~$30/mo | Etsy research tool |
| EverBee subscription | ~$30/mo | Etsy analytics |

## API Costs (Third-Party)

| API | Cost | Usage |
|-----|------|-------|
| ElevenLabs | $22/mo | TTS for video narration |
| kie.ai | Pay-per-use | Veo3, Kling video generation (cloud fallback) |
| Fal.ai | Pay-per-use | Flux 2 cloud fallback |
| OpenRouter | Pay-per-use | Non-Claude model access |

## Developer Benchmarks

| Metric | Value | Source |
|--------|-------|--------|
| Senior dev LOC/hr | 50 | Production-quality, reviewed code |
| Senior dev rate (US) | $100/hr | Market average 2026 |
| Junior dev rate (US) | $40-60/hr | Market average 2026 |
| Claude productivity multiplier | 2.5-5x | Based on Roberto's data |

## Hardware (One-Time, Already Owned)

| Component | Approx Cost | Notes |
|-----------|-------------|-------|
| AMD Ryzen 9 5900X | ~$300 | 12-core/24-thread |
| 64 GB DDR4 RAM | ~$150 | |
| Samsung 990 PRO 2TB | ~$180 | NVMe |
| RTX 3080 Ti 12GB | ~$500 | GPU 0 (Whisper) |
| RTX PRO 6000 96GB | ~$7,000 | GPU 1 (primary) |
