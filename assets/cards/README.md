# Prepared Fate cards

Place the four original PNG files here, with these exact case-sensitive names:

- AV-F001.png — Artoria Pendragon
- AV-F002.png — Morgan le Fae
- AV-F003.png — Enkidu
- AV-L001.png — Kama & Ishtar (one card)

Do not replace them with generated art, URLs, thumbnails or placeholders. Inline
images in a chat are not necessarily downloadable original files in the workspace.
Run `python -m cardlegends.cli check-cards` before building the release image.
Missing/invalid files disable new summons without charging coins. Existing results
and the text interface remain available. Originals are packaged unchanged; Telegram
photo delivery is used within photo limits, otherwise PNG is sent as a document
(up to 50 MiB). No crop, overlay or redraw is performed.
