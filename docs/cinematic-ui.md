# Cinematic UI: images, status bars and navigation

This update adds dynamically generated illustrated Telegram **photo screens** without changing any
existing original Fate card PNG. All scene pictures are rendered in memory with Pillow and the
already bundled DejaVu font. They compose the existing collectible-card artwork with newly drawn
backgrounds, borders, silhouettes, gradients and progress bars.

## Screens

- Home: full-colour gacha-inspired menu, user rank, currency and Fate progress.
- Profile and collection: animated-game-style illustrated previews, real progress meters.
- Summon: Fate banner image and real drop-rate labels; existing randomness is unchanged.
- Squad: illustrations of the existing card portraits and team combat stats.
- Tower: a generated fortress/enemy silhouette with a real HP meter.
- Victory / defeat: result art and the actual remaining squad HP.
- Activities and login calendar: reward-themed artwork and visit progress.

**Telegram limitations:** custom-shaped physical buttons and smooth HP animations cannot be embedded
into standard inline keyboard markup. We use mobile-friendly emoji buttons and render the true
numerical state into pictures and HTML captions instead. Battle calculations are unchanged.

## Interaction and reliability

The view logic edits an existing photo with Telegram \`editMedia\` on illustrated-to-illustrated
navigation; on text-to-photo or photo-to-text transitions it removes the stale screen and posts
one fresh message. Long captions (more than 1024 characters) use a text-only screen. Failures
to generate scene art fall back to working text-only navigation. Original PNG summons are
still delivered using the existing file-id cache, original file bytes, and archiving rules.

This update does not alter economy, migrations, summons, odds, EXP, NP, quests, or tower rewards.
