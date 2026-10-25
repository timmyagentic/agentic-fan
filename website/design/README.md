# Website image sources

`social-card.html` is the editable source for the 1200 × 630 social preview PNG. Serve the website asset directory at `/` and this file at `/__social-card.html`, then capture the complete 1200 × 630 viewport after fonts and images load. The PNG is an unmodified browser capture. It uses this repository’s existing local fonts and public product assets.

`assets/app-overview.png` and `assets/menu-bar.png` render the actual native `MainView` and `MenuPanel` at commit `1035a1ccd45495a3128a8aba02417e833d3b3234`, using a CLI demo snapshot and offscreen SwiftUI hosting. They are native interface renders, not photographs or live hardware readings. The site labels them as demonstration data. No native application behavior was changed.

`brand/fonts/chinese-extra-*.ttf` supplements the existing Noto Sans SC subsets for new page text. The source is [Google Fonts Noto Sans SC](https://github.com/google/fonts/tree/main/ofl/notosanssc), under the existing SIL Open Font License in `brand/fonts/`. The CSS declares only the added Unicode code points.
