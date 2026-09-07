# Third-party notices

The complete Chinese exercise text catalog and terminology are copied from the
companion flutter-init repository. The source dataset is
[hasaneyldrm/exercises-dataset](https://github.com/hasaneyldrm/exercises-dataset).
Its pinned version, count and file checksums are stored in
`src/fitness_bot/data/manifest.json`.

The MIT license for the exercise data is retained as
`src/fitness_bot/data/LICENSE.exercises-dataset`; the original notice is retained
as `src/fitness_bot/data/UPSTREAM_NOTICE.md`.

This backend copies exercise metadata and instructions, including media identifiers.
It does not copy or serve exercise JPG/GIF files. The iOS app resolves its own media.
The upstream media attribution is **© Gym visual — https://gymvisual.com/**;
media is governed separately from the dataset's MIT license.

## Marked

Web Markdown parsing uses Marked 16.4.2 (MIT), bundled locally without a runtime CDN dependency.
Source: https://github.com/markedjs/marked/tree/v16.4.2
License: src/fitness_bot/web/vendor/marked-LICENSE.md
NPM tarball SHA-1: 4959a64be6c486f0db7467ead7ce288de54290a3
Only lexer output is used; application code creates allowlisted DOM elements and text nodes.
