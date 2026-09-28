# Techspire Official - research report: news pipeline, Facebook + Instagram, costs

- **Date:** 2026-09-28.
- **Status:** research and plan only. Nothing was posted, no Meta account was accessed, and publishing stays locked
  (`DRY_RUN=true`, `PUBLISH_ENABLED=false`).
- **Scope:** the brief "Automated news publishing system - research, architecture and cost optimisation": about 3 posts
  a day (90 a month) to Facebook and Instagram.
- **Sources:** official documentation fetched on 2026-09-28, listed at the end as [F..] Facebook, [I..] Instagram,
  [A..] AI pricing, [H..] hosting and [S..] standards. Prices are in US dollars, excluding tax.

## Decisions needed from you

1. **Sectors.** This brief is technology-only (AI, cybersecurity, software, cloud, hardware, gadgets, startups and so
   on) and rejects general business and economics news. The current build follows your earlier request for five sectors
   (Technology, Business, Finance, Career Development, Entrepreneurship). Which one should the product follow? Either way,
   the plan moves the sector list into a config file.
2. **Your source list.** The brief says you will supply the tech sites. The current 19 feeds were chosen for the five
   sectors.
3. **Instagram account.** It must be a professional account (Business or Creator). The recommended setup also links it
   to the Techspire Facebook Page inside a Meta Business portfolio (section 7).
4. **Where it runs after launch:** your laptop (free) or a small server (about $4-7 a month) (section 13).

### Owner decisions (2026-09-28)

These replace the matching parts of the report below.

- **Fully in the cloud, with no laptop.** Option 1 is dropped. A small VPS runs the program on a schedule, and
  approvals come through a Telegram bot on your phone instead of the local review page. The code is still written and
  tested in this project folder; it just doesn't run the page from here.
- **The existing server is a Hostinger Cloud Startup plan.** It is managed web hosting with 4 CPU cores, 4 GB RAM,
  100 GB NVMe, SSH, cron jobs and free SSL [H26][H28]. Hostinger states that "Python is supported exclusively on VPS
  Hosting", because Web and Cloud plans don't provide root access [H27]. It therefore becomes the public web side:
  - it hosts the Instagram images, with random file names, deleted after publishing;
  - it hosts the link-in-bio page;
  - Cloudflare R2 and Pages are no longer needed.
- **VPS candidates:**
  - Hostinger KVM 1: 1 vCPU, 4 GB RAM, 50 GB NVMe; $6.49 a month on a 2-year term, renewing at $11.99 [H29].
  - DigitalOcean's $4 plan: 512 MB, billed monthly [H20].
  - Google Cloud's free e2-micro VM isn't free in practice. The VM costs nothing in three US regions [H7], but its
    public IPv4 address costs US$0.005 an hour, about $3.65 a month [H25].
- **Running cost:** VPS $4-6.49 + AI $1-3 = **about $5-10 a month**. The Hostinger plan is already paid for.

## 1. Executive summary

- **Build on the current system; don't start over.** It already handles the hard, safety-critical parts: hardened RSS
  reading, duplicate protection, validated AI output, Pillow cards, a review page, and a locked Facebook publisher that
  cannot post the same story twice.
- **Ingestion:** each source gets its own reading method, set in one `sources.yaml` file. The order of preference is RSS/Atom
  first, then news sitemaps, then simple HTML listing pages. Bot protection is never bypassed: a source that blocks
  automated access is skipped.
- **AI in two small steps.** A cheap model screens every new article: is it relevant, which category, what kind of event,
  which companies. Plain code then merges same-story duplicates and picks 3 stories a day using written rules. Only
  those 3 go to a better model, which writes the headline, the short overview and both captions. Expected AI cost:
  **$0 on free tiers, or about $1-3 a month paid** when screening 1,000 articles a month.
- **Images:** keep Pillow, but switch to one **1080 x 1350 (4:5)** branded card used on both platforms. 4:5 is Meta's
  recommended Facebook feed ratio and falls within the range Instagram's API accepts. No AI-generated images and no
  publishers' photos.
- **Facebook:** photo post by direct upload (no public URL needed), then a Page comment with the source and link.
  **Comments can be created through the API; pinning them cannot.**
- **Instagram:** create a container, check its status, then publish it. Instagram downloads the image from a **public URL**,
  so each card is placed briefly in cloud storage (Cloudflare R2, free at this volume). **Comments can be created through
  the API; pinning them cannot.** Links in normal Instagram captions and comments aren't clickable, so the publisher is
  credited in the caption and the link goes on a free "link in bio" page.
- **State:** SQLite stays. Each story gets one publication record per platform, so an Instagram failure never re-posts
  to Facebook, and a failed comment is retried without re-posting.
- **Review:** you approve every post at launch. After a few weeks of clean results, low-risk stories could publish
  automatically.
- **Hosting:** build and test on your laptop with Task Scheduler, at no cost. For dependable daily posting at fixed times,
  move to a small server (VPS) costing about $4-7 a month, and approve posts from your phone with a Telegram bot.
- **Running cost** of the recommended setup: about **$5-10 a month**, or **$0.05-0.11 per published post**. It is close to
  $0 if it runs on your laptop with free tiers.

## 2. How the system will work

```text
Source registry (config/sources.yaml)
   |  per source: RSS / news sitemap / HTML listing (or an official API where one exists)
   v
Discovery, every 30 min ....... conditional requests; robots.txt for non-feed pages; blocked -> skip   [code]
   v
Clean text, canonical URL, exact duplicates (URL, title fingerprint)                                     [code]
   v
Pre-filter: age, feed category, blocked sections/keywords                                                [code]
   v
AI step 1 - screen every candidate: relevant? category, event type, companies/products,         [cheap model]
            security severity, funding amount (all checked against the source text)
   v
Same-story clustering + source priority (which publisher gets the credit)                                [code]
   v
Selection at 3 daily slots (written rules, not an AI "importance score")                                 [code]
   v
AI step 2 - for the chosen story only: headline, overview, Facebook caption,                   [better model]
            Instagram caption -> validated
   v
Card 1080 x 1350 (Pillow)                                                                                [code]
   v
Review queue - you approve                                                                              [human]
   v
Publish to Facebook, then Instagram; each platform has its own record                     [code, locked today]
   v
Attribution - publisher named in the caption; link in the first comment (Facebook: clickable)
              and on the link-in-bio page (Instagram)
```

## 3. News ingestion

### Order of preference per source

| Order | Method | Use when | Notes |
|---|---|---|---|
| 1 | Official API | A publisher offers a news API for this kind of use | Rare for news sites; check the terms. |
| 2 | RSS / Atom | Almost every major tech outlet | Published for syndication: title, summary, date, author, categories, link. Already built and hardened. |
| 3 | News sitemap [S2][S3] | No feed, but the sitemap lists new articles | Gives URLs and dates (sometimes titles). Fetch `og:title` / `og:description` for **new URLs only**. |
| 4 | Public JSON endpoint | The site documents a JSON feed | Only where terms and robots.txt allow it. |
| 5 | HTML listing page | Last resort, for simple server-rendered pages | CSS selectors in config; robots.txt checked; never a headless browser. |

A new `--probe-source <url>` command would choose the method automatically. It reads robots.txt, looks for
`<link rel="alternate">` feeds and common feed paths, reads the sitemap index, and reports the best method. This is the
check done by hand for Dhaka Chronicle, which has no feed and a news sitemap.

### Rules for respectful collection

- **robots.txt** ([S1], RFC 9309) is obeyed for every request that isn't the feed itself: sitemaps, article pages and
  listing pages. The current code doesn't read robots.txt yet, because it only fetches feeds.
- **Identify honestly:** a clear User-Agent with a contact address. Make at most about 1 request per second to each host,
  and wait as long as `Retry-After` asks after a 429 or 503 response.
- **Conditional requests** (ETag / Last-Modified) cost almost nothing when a feed hasn't changed. *They aren't in the
  current code yet; add them.*
- **Blocked means stop.** A 403 response, a CAPTCHA or a "checking your browser" page (for example Cloudflare's) means the
  publisher is blocking automation. The source is marked blocked and skipped. No proxies, no faked browser identities,
  no headless browsers.
- **JavaScript-only sites** without a feed or sitemap are skipped in the MVP.
- **Terms of service** are checked once when a source is added and noted in `sources.yaml`. Some sites forbid automated
  access even where robots.txt allows it.
- **Canonical URL:** the feed link with tracking parameters removed (already built). If an article page is fetched, its
  `<link rel="canonical">` takes priority.

### What each article record holds

Title, summary or excerpt, publisher, original URL, canonical URL, publication time, discovery time, author (if given),
feed categories and source method. The existing cleaner already caps untrusted input (20,000 characters of raw HTML, a
1,200-character summary) and strips HTML before anything else sees it.

### Full article text

The full text isn't needed. The feed summary is enough both for screening and for a 2-4 line overview, and skipping the
full text saves bandwidth, copyright exposure and AI tokens. The one exception: if a *selected* story's summary is very
short (under about 200 characters), fetch only that page's metadata (`og:description`), and only where robots.txt allows.

### How often to check

| Interval | Worst-case delay | Feed requests a day (20 sources) | Verdict |
|---|---|---|---|
| 15 min | 15 min | about 1,900 | Only worth it for breaking-news slots |
| **30 min** | 30 min | about 960 (mostly "not modified" once conditional requests are in) | **Default** |
| 60 min | 60 min | about 480 | Fine on a metered connection |

The interval doesn't change AI cost, because each article is screened once whenever it is found. Server cost is
negligible at every interval.

### Source registry

```yaml
sources:
  - id: techcrunch
    name: TechCrunch
    homepage: https://techcrunch.com
    method: rss                  # rss | sitemap | html | api
    url: https://techcrunch.com/feed/
    tier: 2                      # 1 original/primary, 2 major outlet, 3 secondary
    poll_minutes: 30
    skip_categories: [Deals, Sponsored, Podcasts]
    terms_checked: 2026-09-28
    enabled: true

  - id: example-portal
    name: Example Portal
    homepage: https://example.com
    method: sitemap
    url: https://example.com/sitemap-news.xml
    metadata: og                 # read og:title / og:description for new URLs only
    tier: 3
    enabled: true

  - id: example-blog
    name: Example Blog
    homepage: https://example.com
    method: html
    url: https://example.com/tech
    item_link_selector: "article h2 a"
    tier: 3
    enabled: false
```

Each `method` maps to one adapter class with the same interface (`discover() -> list[DiscoveredItem]`). A publisher's
quirks become config fields. A genuinely unusual site gets its own small adapter file, registered by its `id`. Shared
code never contains publisher-specific `if publisher == ...` branches.

## 4. AI strategy

### What the AI does, and what it doesn't

**The AI does:** relevance, category, event facts (type, companies, severity, funding amount), headline, overview and
captions.

**Plain code does:** fetching, duplicate detection, selection, state, images, publishing and retries.

Article text is untrusted data. It goes only into the user turn, wrapped in the existing "untrusted content" framing,
never into the instructions, and the model has no tools.

Every answer is JSON constrained by a schema, which Gemini, OpenAI and Claude all support. Code then checks it:

- every number and web address in the copy must appear in the source;
- the URL must be the original one;
- no invented sources;
- no quotations;
- lengths are within limits;
- no banned hype or advice phrases.

An answer that fails any check is rejected, not "repaired".

### Candidate models (official prices per 1M tokens, checked 2026-09-28)

| Provider | Model | Input | Output | Notes |
|---|---|---|---|---|
| Google | gemini-3.1-flash-lite | $0.25 | $1.50 | Free tier. Shuts down 7 May 2027 [A2]. |
| Google | gemini-3.5-flash-lite | $0.30 | $2.50 | Free tier. **Techspire's current default; tested live.** |
| Google | gemini-3.8-flash | $0.75 | $3.75 | Free tier (20 requests a day seen in testing). Rises to $1.50 / $7.50 on 1 Jan 2027. |
| OpenAI | gpt-6-luna | $0.10 | $0.50 | No free tier [A6]. Already supported by Techspire. |
| Anthropic | Claude Haiku 4.5 | $1.00 | $5.00 | No free tier [A8]. |
| Anthropic | Claude Sonnet 5 | $2.00 | $10.00 | No free tier. Strong writer. Claude token counts may run up to about 30% higher [A8]. |

Output prices include the model's thinking tokens [A1][A6]. All three providers offer a 50% Batch discount, but batch
jobs can take up to 24 hours, which is too slow for news. Gemini's Flex tier is also 50% off, with replies in 1-15
minutes, but it is in preview and may return "busy" errors [A3]. Neither is worth the complexity at this volume.

With billing turned on, the Gemini project pays for all of its usage at the prices above. On the free tier, Google
doesn't publish the limits [A4] and may use the content sent to it to improve its products [A5]. That's acceptable for
public news, but it's a reason to go paid once posting is live.

### Strategy A vs B (and why B+)

- **A - one call per article:** screen and write everything in a single call.
- **B - screen first, then write if relevant.**
- **B+ (recommended) - screen every article, select 3 a day, write only for the selected stories.** That is about 120
  writing calls a month: 90 posts plus about 30 replacements for drafts you reject.

Token assumptions: about 4 characters per token. The current editorial prompt measures 5,730 characters, about 1,430
tokens. An article (title, summary, source, URL) is about 350 tokens.

| Call | Input tokens | Output tokens (including thinking) |
|---|---|---|
| A: screen + write | 2,200 | 500 if relevant, 150 if rejected; with 40% relevant, 290 on average |
| B+: screen | 1,200 | 150 |
| B+: write (120 a month) | 2,100 | 600 |

Monthly AI cost with the same model for all calls, at 300, 1,000 and 3,000 candidate articles a month:

| Model | A: 300 | A: 1,000 | A: 3,000 | B+: 300 | B+: 1,000 | B+: 3,000 |
|---|---|---|---|---|---|---|
| gemini-3.1-flash-lite | $0.30 | $0.99 | $2.96 | $0.33 | $0.70 | $1.75 |
| gemini-3.5-flash-lite | $0.42 | $1.39 | $4.16 | $0.48 | $0.99 | $2.46 |
| gemini-3.8-flash | $0.82 | $2.74 | $8.21 | $0.90 | $1.92 | $4.85 |
| gpt-6-luna | $0.11 | $0.37 | $1.10 | $0.12 | $0.26 | $0.65 |
| Claude Haiku 4.5 | $1.10 | $3.65 | $10.95 | $1.20 | $2.56 | $6.46 |
| Claude Sonnet 5 | $2.19 | $7.30 | $21.90 | $2.39 | $5.12 | $12.92 |

Example of the arithmetic, B+ with gemini-3.5-flash-lite at 1,000 candidates:

- **Input:** (1,000 x 1,200 + 120 x 2,100) = 1.452M tokens x $0.30 = $0.44.
- **Output:** (1,000 x 150 + 120 x 600) = 0.222M tokens x $2.50 = $0.56.
- **Total:** $0.99 a month.

At 300 candidates, the two strategies cost the same (A is marginally cheaper). At 1,000-3,000 candidates, B+ is 30-40%
cheaper, because captions are written only for the few stories you can actually use. The savings are cents to a few
dollars, so **cost isn't the deciding reason**. B+ wins because:

1. A better model can write the few posts that go out.
2. Selection needs screening facts for every candidate anyway.
3. The two steps draw on two different models' free daily limits.

### Recommended pairing

| Setup | 300 candidates/month | 1,000 | 3,000 |
|---|---|---|---|
| Screen with gemini-3.5-flash-lite, write with gemini-3.8-flash, paid | $0.68 | $1.19 | $2.66 |
| The same after 3.8 Flash's price rise on 1 Jan 2027 | $1.14 | $1.65 | $3.12 |
| The same on the free tiers | $0 | $0 | $0 (see the note below) |
| Alternative: screen with gpt-6-luna, write with Claude Sonnet 5 | $1.28 | $1.42 | $1.81 (needs a new Claude module) |

The free tiers cost nothing only while usage stays within their daily limits. That means about 10, 33 or 100 screening
requests a day at the three volumes, and about 4 writing requests a day against the 20 seen in testing.

This pairing is recommended because both models run on your existing key and code, so no new provider is needed.
Flash-Lite already produced accurate copy in the live test, and 3.8 Flash writes better. OpenAI's gpt-6-luna, already
supported, becomes the automatic backup whenever Google returns "high demand" errors. It needs an OpenAI API account
with credit, and it would cost pennies because it runs only as a backup.

### Cheap filtering before the AI

Before any AI call, code drops:

- articles older than 48 hours;
- exact URL and title duplicates;
- feed categories you never want (for example Deals, Sponsored, Podcasts, Reviews);
- titles with non-tech markers you configure.

Keywords never *approve* a story. They only skip obvious non-starters, and the AI still makes every editorial decision.
Expect 20-50% fewer AI calls. That's small in dollars, but it keeps the free tier's daily limit for real candidates.

## 5. Image strategy

| Option | Cost | Quality | Layout reliability | Speed | Maintenance | Brand consistency |
|---|---|---|---|---|---|---|
| **Pillow (current)** | $0 | Good with real fonts | High: text is measured, fits or the card fails | about 0.1 s | Low: Python only | Exact |
| HTML/CSS + Playwright | $0 | Very good typography | High | about 1-2 s | Adds a browser of about 300 MB | Exact |
| SVG + rasteriser | $0 | Good | Font handling is fiddly | Fast | Medium | Exact |
| Canvas (Node.js) | $0 | Good | High | Fast | Adds a second runtime | Exact |
| AI image generation | Paid per image | Unpredictable | Low: text often garbled | Slow | Endless prompt tuning | Weak |

**Recommendation: keep Pillow.** It is already built, with measured word wrapping, a fit-or-fail check (so text is never
clipped), cached layouts and tests. Move to HTML/CSS only if a designer wants to iterate on the look in CSS, or if
Bengali text ever needs proper letter shaping.

### Size

- **Facebook:** the feed accepts 1.91:1, 16:9, 1:1 and 4:5, and Meta **recommends 4:5** [F20]. Photo uploads can be up to
  10 MB [F3]. The current 1200 x 630 card (1.91:1) works, but takes less screen space.
- **Instagram:** the publishing API accepts JPEG only, up to 8 MB, with an aspect ratio **between 4:5 and 1.91:1**, a width
  of 320-1440 px, and sRGB colour [I4]. The app itself now keeps 3:4 photos [I15], but the API doesn't document 3:4, so
  don't rely on it.
- **One universal 1080 x 1350 JPEG (4:5)** meets both requirements. Keep the 1200 x 630 layout only for a future website or
  link previews.

### Card layout (4:5)

From top to bottom:

1. TECHSPIRE OFFICIAL wordmark and logo.
2. Category badge.
3. Headline, up to 4 lines.
4. Overview, up to 4-5 lines.
5. "Source: TechCrunch".
6. A subtle tech-style pattern behind the text.

Layout rules:

- Fixed safe margins of at least 64 px.
- Font sizes step down to a set minimum. If the text still doesn't fit, the story is refused, never clipped.
- The validator limits the overview to about 220 characters.
- Instagram's `alt_text` field (up to 1,000 characters) gets the headline and overview, for accessibility [I4].

## 6. Facebook architecture

- **API version:** Graph API **v26.0** (released 29 Jul 2026) is the newest and already Techspire's default. v20.0
  expired on 24 Sep 2026, and v21.0 lasts until 21 Jan 2027 [F1]. Calls to an expired version are served by the oldest
  version still available [F2], so keep `FB_GRAPH_API_VERSION` current.
- **Post:** `POST /{page-id}/photos` [F3] with:
  - the JPEG as a multipart upload, so no public URL is needed;
  - `caption` (the old `message` field is deprecated);
  - `published=true`.

  It returns `{id, post_id}`. Techspire already does exactly this.
- **Comment:** `POST /{post-id}/comments` with `message` returns `{id}` [F4][F15]. One reference page lists creating
  comments as unsupported [F16], while the main comments reference documents it, so confirm it on the first supervised
  post. This is already built, including a search for an existing comment before any retry. Change the text from
  "Read full story: <url>" to "Source: <Publisher> - Read the original story: <url>".
- **Permissions:**
  - Posting needs a Page token from someone with the "create content" task, plus `pages_manage_posts`,
    `pages_read_engagement` and `pages_show_list` [F3].
  - Commenting needs the "moderate" task plus `pages_manage_engagement`, which in turn needs `pages_read_user_content`
    [F4][F5].
- **App access:**
  - An app used only by people with a role on it needs only Standard Access: no App Review and no Business
    Verification [F6][F7].
  - Don't leave the app in Development mode: posts made there are visible only to people with a role on the app [F8].
    A Business-type app has no modes [F9].
- **Token:**
  - Either a System User token from Meta Business Suite (never-expiring, or 60-day expiring and refreshable, which Meta
    recommends) [F11], or a Page token from a long-lived user token, which has no expiry date but can be invalidated
    [F10].
  - Tokens stop working after a password change, a removed Page role, de-authorisation, security events, or 90 days of
    app inactivity [F12][F13][F14]. Techspire already stops publishing on token errors (190/102) and permission errors
    (10, 200-299).
- **Limits:** Page tokens get 4,800 calls x the number of engaged users per rolling 24 hours [F19], which is irrelevant
  at 6 writes a day. Error 506 means duplicate post; 368 means a policy block [F13].

## 7. Instagram architecture

- **Account:** a professional account, either Business or Creator.
- **Two official setups** [I1][I2]:

| | Instagram Login | Facebook Login for Business (**recommended**) |
|---|---|---|
| Linked Facebook Page | Not required | Required |
| API host | graph.instagram.com | graph.facebook.com |
| Permissions | instagram_business_basic, instagram_business_content_publish, instagram_business_manage_comments | instagram_basic, instagram_content_publish, instagram_manage_comments, pages_show_list, pages_read_engagement (plus ads_read / ads_management if the Page role comes through a Business portfolio) |
| Token | 60 days; refresh once it is 24 hours old and before it expires [I12][I13] | The same System User or Page token as Facebook |

The recommended setup is Facebook Login for Business, with the Instagram account linked to the Techspire Page. One Meta
app and one System User token then serve both platforms. App Review isn't required for "an app only for a business I
own or manage" [I14].

### Publishing a single image [I3][I4][I5][I6]

1. `POST /{ig-user-id}/media` with:
   - `image_url`: a public HTTPS address, because Meta downloads the image itself;
   - `caption`: up to 2,200 characters and 30 hashtags;
   - `alt_text`: up to 1,000 characters.

   This returns a container ID.
2. `GET /{container-id}?fields=status_code` about once a minute, for up to 5 minutes, until it returns `FINISHED`. The
   other values are `IN_PROGRESS`, `PUBLISHED`, `ERROR` and `EXPIRED`.
3. `POST /{ig-user-id}/media_publish` with `creation_id` returns the media ID.

Other rules:

- **Containers:** they expire after 24 hours, and you can create up to 400 per rolling 24 hours [I4].
- **No direct image upload:** none is documented for images, so the image must be at a public URL [I3].
- **Publishing limit:** the docs disagree, saying 50 or 100 posts per 24 hours [I3][I6]. Read
  `GET /{ig-user-id}/content_publishing_limit` before publishing [I7]. 3 a day is far below either figure.
- **Duplicate protection:** Meta doesn't say a container can be published only once, so Techspire must enforce it:
  1. Save the container ID before publishing.
  2. After a timeout, check the container before doing anything else.
  3. `PUBLISHED` means the post went out: look up the media ID and never publish again.
  4. `FINISHED` means it didn't go out, so it is safe to publish once more.

### Hosting the image for Instagram

| Service | Free allowance | Card needed | How Meta gets the image | Verdict |
|---|---|---|---|---|
| **Cloudflare R2** | 10 GB-month, 1M writes, 10M reads a month, free egress [H1] | Yes | Presigned link, valid from 1 second to 7 days, from the S3 endpoint [H3] | **Recommended** |
| Cloudinary | 25 credits a month (1 credit = 1 GB storage, 1 GB bandwidth or 1,000 transformations) [H4] | No | Delivery URL | Best option without a card |
| Supabase Storage | 1 GB storage, 5 GB egress; free projects pause after a week of inactivity [H5] | Not stated | Public bucket URL | Pausing risk |
| AWS S3 | No permanent free S3 allowance; the credit-based free plan ends within 6 months [H6] | Yes | Presigned URL | Overkill |
| Google Cloud Storage | 5 GB-month in three US regions; 100 GB a month egress from North America [H7] | Billing account | Signed URL | Workable, but more setup |

The recommended flow:

1. Upload the card to a **private** R2 bucket.
2. Give Instagram a presigned link that lasts about an hour.
3. Delete the file after the post is published.

About 90 images a month (roughly 30 MB) is far inside the free allowance. Avoid R2's r2.dev public addresses, which are
rate-limited and meant for development only [H2]. Confirm that Meta accepts the presigned link on the first supervised
post.

**Update:** Techspire's own Hostinger site now takes this role (see "Owner decisions" at the top). The same rules
apply: random file names, and each file deleted after publishing.

### Publishing to both platforms

"Concurrent" means both platforms are published in the same job, one after the other. Facebook goes first because it
needs no image hosting. Neither platform waits for the other to succeed, and each has its own record (section 12).

## 8. Source comment and pinning

| Platform | Create comment through the API | Pin comment through the API | Official endpoint and limitation |
|---|---|---|---|
| Facebook | **YES** | **NO** | `POST /{post-id}/comments` [F4]. A comment's only updatable fields are the message, the attachment and `is_hidden`; there is no pin field [F17]. Pinning a *post* has an undocumented `is_pinned` parameter [F18] that developers report failing, so it isn't usable. |
| Instagram | **YES** | **NO** | `POST /{ig-media-id}/comments` [I8]. Comment updates support only hiding, and your own comments are always shown anyway [I9]. There is no pin field on comments or media [I10][I11]. |

No unofficial workaround, such as browser automation or private APIs, is recommended.

### The closest compliant approach

- **Facebook:**
  - The caption ends with "Source: TechCrunch": short and always visible.
  - The first comment says "Source: TechCrunch - Read the original story: <link>". In practice Facebook turns links in
    comments into clickable links; Meta's API docs don't say so, so check on the first post.
  - If you want the comment pinned, pin it by hand in the app. Techspire's daily summary can list each new post's
    address so this takes seconds.
- **Instagram:** links in normal post captions and comments are not clickable. Meta documents clickable links only in
  the bio (up to 5 links), in story link stickers, and as a paid Meta subscription feature for posts [I16][I17][I18]. So:
  - **Caption:** "Source: TechCrunch · Full story: link in bio", which gives clear credit.
  - **First comment:** "Source: TechCrunch - <link>", for completeness. Pin it by hand if you want; the Instagram app
    lets you pin comments on your own posts.
  - **Link in bio:** points to a free Techspire "Latest stories" page that Techspire regenerates after each post, newest
    first, with the headline, the publisher and a clickable link to the original. It can live on Techspire's Hostinger
    site (the current plan), or for free on Cloudflare Pages (no card, unlimited bandwidth, a `*.pages.dev` address)
    [H8][H9]. GitHub Pages isn't suitable: its free plan needs a
    public repository and doesn't allow commercial sites [H10]. This page is the only option that gives Instagram
    readers a real one-tap route to the original story.

## 9. Copyright and scraping

This is practical risk guidance, not legal advice.

- **Facts aren't owned; wording and photos are.** Write original short summaries from the facts. Never copy sentences,
  use no quotations, and always credit and link the publisher.
- **Don't reuse publishers' images.** News photos are licensed, often from agencies that actively enforce their rights.
  Reposting them is the most common way small pages end up with takedowns or claims. The branded Techspire card avoids
  the problem completely, costs nothing and keeps the look consistent.
- **Each publisher's terms still apply.** Feeds are offered for syndication, but record a terms check for each source in
  `sources.yaml`.
- **Respect blocks.** Obey robots.txt for everything that isn't the feed itself, and never bypass Cloudflare or any other
  bot protection.
- **Keep full text out of the system.** The title and summary are enough, which also keeps AI costs low.

## 10. Same-story deduplication

Different outlets covering the same event should produce one Techspire post.

1. **Exact duplicates** (already built): canonical URL and title fingerprint.
2. **Same event** (new; plain code, no AI call). Each relevant candidate is compared with candidates and published
   stories from the last 72 hours on three signals:
   - title word overlap (Jaccard similarity of lower-cased words, with common words removed);
   - companies or products shared in the screening results;
   - the same event type (launch, funding, acquisition, security incident and so on).

   Two stories count as the same if they share at least one entity, have a title overlap of at least 0.35 and have the
   same event type, **or** if their title overlap is at least 0.6. The thresholds get tuned on a week of real data.
3. **Optional later:** for borderline pairs only, compare text embeddings (Gemini embeddings cost $0.20 per 1M tokens,
   or use the free tier [A1]). This isn't needed at tens of articles a day, and no vector database is needed either.
4. **Already covered:** if a story's group was published in the last 7 days, skip it unless you mark it as a follow-up.

### Which article gets the credit (source priority)

1. **Tier 1:** the original announcement, such as a company newsroom or blog, or an official security advisory.
2. **Tier 2:** a major news outlet.
3. **Tier 3:** a secondary publication.

Ties go to the earliest published article, then to the richer summary. The chosen article's publisher and URL become
the attribution. The other articles are recorded as "also reported by" and never replace the credit.

## 11. Story selection

Posts go out in three fixed slots, for example 09:00, 14:00 and 20:00 Dhaka time. At each slot, the highest-scoring
relevant story from the last 24 hours that hasn't been used yet is written up and sent for review. If you reject it, the
next best is written and offered instead.

The score uses only facts, not an AI opinion:

| Factor | Points |
|---|---|
| Freshness: under 6 h / under 12 h / under 24 h | +3 / +2 / +1 |
| Covered by 2 / 3 or more different publishers (from clustering) | +1 / +2 |
| Source is tier 1 or 2 | +1 |
| A company on your "major companies" list (in config: Apple, Google, Microsoft, OpenAI, Meta, Amazon, Nvidia, Samsung, TSMC and so on) | +1 |
| Acquisition, or a product launch by a listed major company | +2 |
| Security: actively exploited flaw or major breach, as stated in the source | +3 |
| Funding of $100M or more / $20M or more (the amount must appear in the source) | +2 / +1 |
| Same category as one of today's earlier posts | -2 |
| Opinion, review, deal or sponsored content | Excluded |

The AI supplies only the categorical facts: event type, severity, companies and amount. Each is checked against the
source text where possible. The arithmetic is done in code, and the review page shows it ("why this story").

## 12. Database and state

SQLite is right for this scale: a few hundred rows a day and one writer. It is already in use with WAL mode, state
changes that succeed only if the state hasn't changed in between, an audit log and a single-run lock. PostgreSQL isn't
needed unless several machines have to write at the same time.

### Proposed schema (version 3)

It would be upgraded automatically, like the earlier version 1 to 2 upgrade.

| Table | Holds |
|---|---|
| `sources` | The registry plus health: last success, consecutive failures, blocked reason |
| `articles` (exists) | Every discovered item: title, summary, URLs, times, source |
| `screenings` | AI step 1 results: relevant, category, event type, companies, severity, amount, model, prompt version |
| `story_clusters` | Same-event groups, the article that gets the credit, and whether the group was published |
| `drafts` | AI step 2 results: headline, overview, both captions, source name and URL, review status |
| `assets` | Rendered cards: path, SHA-256, size, and the hosted URL and its expiry for Instagram |
| `publications` | **One row per story per platform**: status, container ID, post or media ID, comment ID, attempts, last error |
| `events`, `runs`, `run_lock` (exist) | Audit trail, run history, single-run lock |

### Status of each publication

- **Facebook:** `PENDING -> PUBLISHING -> POSTED -> COMMENTING -> DONE`.
- **Instagram:** `PENDING -> CONTAINER_READY -> PUBLISHING -> POSTED -> COMMENTING -> DONE`.
- **Both platforms:**
  - `FAILED` means it is safe to retry the failed step.
  - `UNKNOWN` means the request may have succeeded, so the outcome is checked before any retry.

### Duplicate-proof rules

These carry over from the current Facebook design and apply to each platform.

- **Save IDs immediately.** An ID is saved the moment a platform returns it.
  - A database trigger forbids clearing or replacing a saved post ID. The existing `prevent_second_upload` trigger
    becomes per-platform.
  - A unique key allows only one publication per story per platform.
- **Retry only the failed step.** A failed comment retries only the comment. A failed Instagram publish retries only
  Instagram. A Facebook row marked `POSTED` is never touched again.
- **Look before retrying anything unclear:**
  - Facebook comment: search the post's comments for Techspire's own (built).
  - Instagram publish: check the container status.
  - Facebook photo: you confirm on the Page, using `--resolve-unknown` (built).

| Situation from the brief | Next run |
|---|---|
| Facebook posted, Instagram failed, app crashed | Facebook row is `POSTED`/`DONE`, so it is skipped. Instagram row is `FAILED`, so only Instagram is retried. |
| Facebook posted, Facebook comment failed | Facebook row is `COMMENTING`/`FAILED`, so only the comment is retried, on the saved post ID. |
| Instagram publish timed out | Row is `UNKNOWN`. Container shows `PUBLISHED`: record the media ID. Container shows `FINISHED`: publish once more. |

### Failure handling

| Failure | Behaviour |
|---|---|
| Website down or feed malformed | Skip that source this run, log it and count the failure; after 5 failures in a row, mark it unhealthy |
| AI unavailable or rate-limited | Try the backup model or provider; if that also fails, articles wait for the next run |
| Card rendering fails | The story stays approved and is retried next run; nothing is ever published without its image |
| Image upload to storage fails | Instagram waits; Facebook doesn't need the upload and goes ahead |
| Facebook or Instagram error | Only that platform's row fails; token and permission errors stop that platform and alert you |
| Comment fails | Only the comment is retried, up to the retry limit |
| Pinning | Not automated, so nothing to fail |
| Expired Meta token | All publishing for that platform stops; `--doctor` and the daily summary report it |

## 13. Hosting and scheduling

| Option | Monthly cost | Reliability | Notes |
|---|---|---|---|
| **A. Your laptop** + Windows Task Scheduler | $0, plus electricity | Runs only while the laptop is on and awake | Good for building, testing and hands-on review; no approving from elsewhere |
| **B. Small VPS** + systemd timers | DigitalOcean: $4 (512 MB RAM, IPv4 included) [H20]. Hetzner CX23: €5.99 / $7.09 including IPv4 (2 vCPU, 4 GB; price rose on 15 Jun 2026) [H18][H19] | Always on | The simplest dependable option. The SQLite file lives on disk and Pillow runs fine. 512 MB is tight but workable; 4 GB leaves plenty of room. |
| **C. Serverless** (Cloud Run jobs + Cloud Scheduler, or AWS Lambda + EventBridge) | Free tiers cover the compute [H14][H15][H16] | High | No persistent disk, so SQLite doesn't fit; it would need a hosted database and more setup |
| **D. GitHub Actions** | Private repositories get 2,000 free minutes a month; a 30-minute schedule uses about 1,460 [H11] | Scheduled runs can be delayed or dropped [H12] | SQLite would have to live in the cache, whose entries can't be overwritten and are deleted after 7 unused days [H13]. Awkward and risky. |
| Oracle Always Free VM | $0 | Idle machines are reclaimed when all usage stays under 20% for 7 days [H21] | A mostly idle job is exactly what gets reclaimed |
| Google Cloud free e2-micro VM | The VM is free in three US regions [H7], but its public IPv4 address costs US$0.005 an hour, about $3.65 a month [H25] | High | Only 1 GB of free outbound traffic a month; more setup than a plain VPS for about the same price |
| Cloudflare Workers | Free: 10 ms of CPU per scheduled run [H17] | High | Too little CPU for Pillow, and no SQLite file |

Things that are easy to miss:

- Meta's APIs don't need a fixed IP address.
- Logs and the database fit on any disk.
- Instagram needs image hosting in every option (section 7).
- On the laptop, the scheduled task needs "wake the computer to run this task" switched on.

**Recommendation (updated after the owner's decision to run fully in the cloud):** a small VPS runs the program, and
Techspire's Hostinger Cloud plan hosts the public images and the link-in-bio page. The code is still written and tested
in this project folder, but the page is never run from the laptop.

## 14. Monthly cost estimate (3 posts a day, 90 a month)

| Item | Ultra-low-cost | Recommended | Notes |
|---|---|---|---|
| AI screening and writing | $0 (free tiers) | $0.68-2.66 (300-3,000 candidates) | Section 4; slightly higher after 1 Jan 2027 |
| Hosting | $0 (laptop) | $4-7.09 (VPS) | Section 13 |
| Image hosting for Instagram | $0 (R2 or Cloudinary free) | $0 (R2 free) | About 30 MB a month |
| Link-in-bio page | $0 | $0 | Cloudflare Pages |
| Database, scheduler, crawler, rendering | $0 | $0 | SQLite, cron / Task Scheduler, Python, Pillow |
| Bandwidth | $0 | $0 | A few GB a month at most; VPS plans include far more |
| Meta APIs | $0 | $0 | |
| Monitoring | $0 | $0 | healthchecks.io free: 20 checks [H22] |
| Approvals by phone | Not used | $0 | The Telegram Bot API is free [H23] |
| Domain | Not needed | Optional, not priced here | `*.pages.dev` works without one |
| **Total** | **about $0** | **about $5-10** | |
| **Per published post** | **about $0** | **about $0.05-0.11** | ($4-7.09 + $0.68-2.66) / 90 |

- **Required costs:** none for the ultra-low-cost option. The recommended option's only required cost is the VPS.
- **Optional costs:** paid AI instead of the free tiers, and a domain.
- **Free:** everything else.

## 15. Three architecture options

### Option 1 - Ultra-low-cost (about $0 a month)

- **Architecture:**
  - Your laptop with Task Scheduler, SQLite, and the RSS and sitemap adapters.
  - Gemini free tier: Flash-Lite screens and 3.8 Flash writes, with gpt-6-luna as an optional paid backup.
  - Pillow 4:5 cards.
  - Cloudinary free (no card) or R2 for Instagram images.
  - Local review page, pinning by hand, and a link-in-bio page on Cloudflare Pages.
- **Advantages:** free; mostly built already; everything stays on your machine.
- **Disadvantages:** it posts only while the laptop is on; free-tier limits are unpublished and can change; Google may
  use free-tier inputs; you can't approve posts away from the laptop.
- **Maintenance:** low, but you are the scheduler.

### Option 2 - Recommended (about $5-10 a month)

- **Architecture:**
  - A small VPS (Hetzner CX23 or DigitalOcean $4) running the same Python app on systemd timers.
  - SQLite, with a nightly backup to R2.
  - Gemini paid tier (Flash-Lite screens, 3.8 Flash writes), with gpt-6-luna as the automatic backup.
  - Pillow 4:5 cards, and R2 presigned links for Instagram.
  - A Telegram bot for approvals: preview image plus Approve/Reject, accepted only from your account.
  - A healthchecks.io heartbeat, and a link-in-bio page on Cloudflare Pages.
- **Advantages:** posts go out on time every day; you approve from your phone; the paid AI tier gives predictable limits
  and your inputs aren't used for training; it still costs very little.
- **Disadvantages:** a server to keep updated (automatic security updates plus a monthly check); secrets live on the
  server.
- **Maintenance:** about 15 minutes a month.

### Option 3 - More scalable (only if Techspire grows a lot)

- **Architecture:**
  - A containerised app on Cloud Run jobs with Cloud Scheduler, or a bigger VPS.
  - Managed PostgreSQL instead of SQLite, and R2 for images.
  - A small web dashboard with logins for several editors.
  - Embedding-based deduplication, the Batch API for bulk screening, and settings for more pages.
- **Monthly estimate:**
  - Compute stays largely within free tiers [H14][H15].
  - A managed database and dashboard hosting add paid plans, not priced in this research.
  - AI cost grows roughly in line with the number of candidates (section 4).
- **Advantages:** several editors, several pages, tens of posts a day.
- **Disadvantages:** several services, more configuration, higher bills and more to secure.
- **Maintenance:** noticeably higher.

**Recommendation: Option 2, fully in the cloud** (the owner's decision). A VPS runs the program, the Hostinger plan
hosts the public images and the link-in-bio page, and approvals come through Telegram. Option 3 solves problems
Techspire doesn't have at 3 posts a day.

## 16. Recommended stack

- **Python and libraries:** Python 3.14 (the current virtual environment), httpx, feedparser, BeautifulSoup (page
  metadata and the HTML adapter only), Pydantic, PyYAML and Pillow. All are already in the project.
- **Database:** SQLite from the standard library, in WAL mode, with schema version 3.
- **AI:**
  - google-genai for Gemini 3.5 Flash-Lite and 3.8 Flash, and openai for the gpt-6-luna backup. Both are already
    integrated.
  - Claude Sonnet 5 only if you want it as the writer, which needs a new module.
- **Images:** a Pillow 4:5 renderer, with brand fonts under their open licences (OFL fonts are already bundled).
- **Instagram hosting:** Cloudflare R2 through its S3-compatible API, using presigned download links.
- **Meta:** Graph API v26.0 through plain httpx calls, with no SDK. The token goes only in the Authorization header,
  as it does today.
- **Scheduling:** Windows Task Scheduler on the laptop, then systemd timers on the VPS.
- **Approvals:** the local review page first, then the Telegram Bot API over httpx.
- **Observability:** the existing rotating local logs, one summary line per run, a daily digest, and a healthchecks.io
  heartbeat that alerts you if runs stop. At this scale nothing else is needed: no Kafka, Redis, Celery, Kubernetes,
  Elasticsearch, vector database or microservices.

Logged for every run:

- articles discovered, exact duplicates skipped and articles pre-filtered;
- articles screened, rejected and merged as the same story;
- stories selected, drafts written and cards rendered;
- post and comment successes and failures on each platform;
- retries, and token or permission errors.

## 17. Recommended MVP

Build in this order, entirely in dry-run mode, so nothing is published:

1. **Ingestion:**
   - `sources.yaml` and the adapter interface, with RSS moved over;
   - the news-sitemap adapter and `--probe-source`;
   - conditional requests, and robots.txt for non-feed requests.
2. **Screening:** the pre-filter, AI step 1, same-story clustering and source priority.
3. **Selection and writing:**
   - selection with 3 daily slots;
   - AI step 2, producing the headline, overview, Facebook caption, Instagram caption and source line;
   - extended validators.
4. **Card:** the 1080 x 1350 card with the overview and source line.
5. **Review:**
   - schema version 3 with per-platform publication records;
   - Telegram approvals: each draft reaches your phone with the card, both captions, the source and "why this story",
     plus Approve and Reject buttons. The local review page stays for development.
6. **Instagram:** the Instagram publisher and R2 hosting, tested only against mocks and kept behind the same publishing
   lock as Facebook.

Only with your explicit, separate go-ahead would the next steps happen: setting up the Meta app and tokens, and a first
supervised live post on each platform.

### Review mode

- **Start with Mode B: approve every post.** It takes about a minute per post and protects the page from the realistic
  failures: a wrong category, an overstated headline, or a story that was later corrected.
- **After 4-6 weeks, consider Mode C (hybrid)** if nearly everything is being approved unchanged. A story would
  publish automatically only when:
  - every check passes;
  - the source is tier 1 or 2;
  - the story isn't in a sensitive group: security incidents that name victims, legal cases, deaths or injuries,
    layoffs, financial figures, politics or regulation.

  Everything else would still wait for you.
- **Mode A (fully automatic) isn't recommended** for a news page.

## 18. Future improvements (can wait)

- Hybrid auto-publishing (Mode C), after the trial period.
- Embeddings for borderline same-story pairs.
- Fetching page metadata for selected stories with thin summaries.
- Read-only post statistics (reach, clicks) to tune the selection weights.
- An HTML/CSS renderer, if Bengali cards or a designer's CSS are wanted.
- More pages or languages.
- Instagram carousel posts (several cards per story).

## 19. Implementation plan

### Repository structure

Evolve the current package, and let each existing module keep its tests. Files are moved first, in one step that
doesn't change behaviour, while all 325 tests stay green.

```text
techspire/
├── main.py                 CLI (exists) + --probe-source, --select, --approve; --publish stays locked
├── config.py               settings (exists) + Instagram, storage, slots, selection
├── enums.py, models.py     (exist) + Platform, PublicationStatus, EventType
├── storage.py              SQLite (exists) -> schema v3: screenings, clusters, drafts, assets, publications
├── fileio.py, logging_config.py, url_utils.py, text_cleaner.py      (exist, unchanged)
├── sources/
│   ├── registry.py         NEW    loads and validates config/sources.yaml; source health
│   ├── http.py             MOVED  hardened HTTP client + SSRF guard (from rss_fetcher.py) + conditional requests
│   ├── robots.py           NEW    robots.txt (RFC 9309) cache and checks for non-feed requests
│   ├── rss.py              MOVED  feed parsing (from rss_fetcher.py)
│   ├── sitemap.py          NEW    news-sitemap adapter + og: metadata for new URLs
│   ├── html.py             LATER  selector-driven listing adapter (config only)
│   └── probe.py            NEW    recommends the method for a new site
├── editorial/
│   ├── prefilter.py        NEW    age, feed categories, blocked keywords
│   ├── providers.py        MOVED  Gemini / OpenAI clients (from content_filter.py) + fallback chain
│   ├── screening.py        NEW    AI step 1
│   ├── clustering.py       NEW    same-story groups and source priority
│   ├── selection.py        NEW    scoring and daily slots
│   ├── writer.py           NEW    AI step 2
│   └── validation.py       MOVED  output checks (from content_filter.py) + new fields
├── media/
│   ├── renderer.py         MOVED  image_generator.py + 1080 x 1350 layout
│   └── hosting.py          NEW    R2 upload, presigned link, delete after publishing
├── publishers/
│   ├── gates.py            MOVED  publishing lock (closed_gates, authorize_publishing, PublishPermit), per platform
│   ├── facebook.py         MOVED  fb_publisher.py
│   └── instagram.py        NEW    container -> status -> publish -> comment, with reconciliation
├── review/
│   ├── preview.py          MOVED  review page: both platform previews, score explanation
│   └── telegram.py         LATER  approvals by phone
└── pipeline.py             (exists) split into stages: discover, screen, select, write, render, publish

config/
├── sources.yaml            replaces feeds.yaml
└── editorial.yaml          NEW    sectors/categories, blocked topics, major-company list, weights, slots
```

### Phases

| Phase | Work | Needs from you |
|---|---|---|
| 0 | Decisions (top of this report) and the final source list | Sector choice, your sites, Instagram account type, choice of VPS |
| 1 | Move files without changing behaviour, then ingestion: registry, sitemap, probe, conditional requests, robots.txt | Nothing |
| 2 | Editorial: pre-filter, screening, clustering, selection, writer, validators | Approve the copy style on samples |
| 3 | 4:5 card, schema version 3, per-platform records, Telegram approvals | Approve the card design; create a Telegram bot |
| 4 | Instagram publisher; upload of images and the link-in-bio page to the Hostinger site; mocked tests only; still locked | An upload-only account on the Hostinger plan |
| 5 | Cloud deployment in dry-run mode: VPS setup and hardening, scheduled runs, heartbeat, nightly backups | VPS access by SSH key (never a password in chat) |
| 6 | Launch: Meta Business portfolio, Business-type app, System User token, first supervised posts | **Explicit, separate permission to publish** |

Every phase keeps the existing rules: tests never touch the network, the publishing lock stays intact, and
`progress.md` is updated at each milestone.

## Sources (fetched 2026-09-28)

### Meta - Facebook

- [F1] Graph API versions - https://developers.facebook.com/docs/graph-api/changelog/versions
- [F2] Versioning guide - https://developers.facebook.com/docs/graph-api/guides/versioning
- [F3] Page photos - https://developers.facebook.com/docs/graph-api/reference/page/photos/
- [F4] Object comments - https://developers.facebook.com/docs/graph-api/reference/object/comments/
- [F5] Permissions reference - https://developers.facebook.com/docs/permissions
- [F6] Access levels - https://developers.facebook.com/docs/graph-api/overview/access-levels/
- [F7] Business verification - https://developers.facebook.com/docs/development/release/business-verification
- [F8] App modes - https://developers.facebook.com/docs/development/build-and-test/app-modes
- [F9] App types - https://developers.facebook.com/docs/development/create-an-app/app-dashboard/app-types
- [F10] Long-lived tokens - https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived
- [F11] System user tokens - https://developers.facebook.com/docs/business-management-apis/system-users/install-apps-and-generate-tokens
- [F12] Token errors - https://developers.facebook.com/docs/facebook-login/access-tokens/debugging-and-error-handling
- [F13] Error handling - https://developers.facebook.com/docs/graph-api/guides/error-handling/
- [F14] App states - https://developers.facebook.com/docs/development/create-an-app/app-dashboard/app-states
- [F15] Pages API comments - https://developers.facebook.com/docs/pages-api/comments-mentions
- [F16] Page post comments reference - https://developers.facebook.com/docs/graph-api/reference/page-post/comments/
- [F17] Comment reference - https://developers.facebook.com/docs/graph-api/reference/comment/
- [F18] Page post reference - https://developers.facebook.com/docs/graph-api/reference/page-post/
- [F19] Rate limiting - https://developers.facebook.com/docs/graph-api/overview/rate-limiting/
- [F20] Feed aspect ratios (Meta Business Help) - https://www.facebook.com/business/help/682655495435254

### Meta - Instagram

- [I1] Platform overview - https://developers.facebook.com/docs/instagram-platform/overview
- [I2] API with Instagram Login - https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login
- [I3] Content publishing - https://developers.facebook.com/docs/instagram-platform/content-publishing
- [I4] IG User media - https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media
- [I5] IG Container - https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-container
- [I6] media_publish - https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media_publish
- [I7] content_publishing_limit - https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit
- [I8] IG Media comments - https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-media/comments
- [I9] IG Comment - https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-comment
- [I10] IG Media - https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-media
- [I11] Comment moderation - https://developers.facebook.com/docs/instagram-platform/comment-moderation
- [I12] Business login tokens - https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login
- [I13] Refresh access token - https://developers.facebook.com/docs/instagram-platform/reference/refresh_access_token
- [I14] App review - https://developers.facebook.com/docs/instagram-platform/app-review
- [I15] Photo sizes in the app - https://help.instagram.com/1631821640426723
- [I16] Links in bio - https://help.instagram.com/362497417173378
- [I17] Story link stickers - https://help.instagram.com/192168966243613
- [I18] Links on posts (Meta subscription) - https://www.meta.com/help/subscriptions/1121274400471172/

### AI pricing and terms

- [A1] Gemini pricing - https://ai.google.dev/gemini-api/docs/pricing
- [A2] Gemini deprecations - https://ai.google.dev/gemini-api/docs/deprecations
- [A3] Gemini Flex inference - https://ai.google.dev/gemini-api/docs/flex-inference
- [A4] Gemini rate limits - https://ai.google.dev/gemini-api/docs/rate-limits
- [A5] Gemini API terms - https://ai.google.dev/gemini-api/terms
- [A6] OpenAI gpt-6-luna - https://developers.openai.com/api/docs/models/gpt-6-luna
- [A7] OpenAI pricing - https://developers.openai.com/api/docs/pricing
- [A8] Anthropic pricing - https://platform.claude.com/docs/en/about-claude/pricing

### Hosting, storage and scheduling

- [H1] Cloudflare R2 pricing - https://developers.cloudflare.com/r2/pricing/
- [H2] R2 public buckets - https://developers.cloudflare.com/r2/buckets/public-buckets/
- [H3] R2 presigned URLs - https://developers.cloudflare.com/r2/api/s3/presigned-urls/
- [H4] Cloudinary pricing - https://cloudinary.com/pricing
- [H5] Supabase pricing - https://supabase.com/pricing
- [H6] AWS free tier FAQ - https://aws.amazon.com/free/free-tier-faqs/
- [H7] Google Cloud free features - https://docs.cloud.google.com/free/docs/free-cloud-features
- [H8] Cloudflare Pages limits - https://developers.cloudflare.com/pages/platform/limits/
- [H9] Cloudflare Pages - https://www.cloudflare.com/products/pages/
- [H10] GitHub Pages limits - https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits
- [H11] GitHub Actions billing - https://docs.github.com/en/billing/concepts/product-billing/github-actions
- [H12] GitHub Actions schedules - https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- [H13] GitHub Actions caching - https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching
- [H14] Cloud Run pricing - https://cloud.google.com/run/pricing
- [H15] Cloud Scheduler pricing - https://cloud.google.com/scheduler/pricing
- [H16] AWS Lambda pricing - https://aws.amazon.com/lambda/pricing/
- [H17] Cloudflare Workers limits - https://developers.cloudflare.com/workers/platform/limits/
- [H18] Hetzner cost-optimized servers - https://www.hetzner.com/cloud/cost-optimized/
- [H19] Hetzner price adjustment - https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/
- [H20] DigitalOcean Droplets - https://www.digitalocean.com/pricing/droplets
- [H21] Oracle Always Free - https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- [H22] healthchecks.io pricing - https://healthchecks.io/pricing/
- [H23] Telegram bots FAQ - https://core.telegram.org/bots/faq
- [H25] Google Cloud external IPv4 pricing - https://cloud.google.com/vpc/pricing-announce-external-ips

### Hostinger

- [H26] Cloud hosting plans - https://www.hostinger.com/cloud-hosting
- [H27] Is Python supported at Hostinger? - https://www.hostinger.com/support/3648030-is-python-supported-at-hostinger
- [H28] Plan parameters and limits - https://www.hostinger.com/support/6976044-parameters-and-limits-of-hosting-plans-in-hostinger/
- [H29] VPS hosting - https://www.hostinger.com/vps-hosting

### Standards

- [S1] RFC 9309, Robots Exclusion Protocol - https://www.rfc-editor.org/rfc/rfc9309
- [S2] Sitemaps protocol - https://www.sitemaps.org/protocol.html
- [S3] Google News sitemaps - https://developers.google.com/search/docs/crawling-indexing/sitemaps/news-sitemap
