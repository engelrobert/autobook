// Autobook template — Fotobuch template + Autobook additions (search for AUTOBOOK).
// Settings like preview overlays and the appendix are now in your
// YAML config file. Run `fotobuch config` to see all options.

// ════════════════════════════════════════════════════════════════════
//  TEMPLATE — edit below only if you know Typst well
// ════════════════════════════════════════════════════════════════════

// Final mode: true = print output, no watermarks or guide lines
//             false = preview with watermark, bleed and margin overlays
#let is_final = false

#let project_name = "{project_name}"
#let data = yaml(project_name + ".yaml")
#set text(font: "Libertinus Serif")

// ── Read settings from YAML config (with defaults for backward compat) ──

// Preview overlay flags (all suppressed in final mode)
#let show_preview_watermark = data.config.preview.at("show_preview_watermark", default: true) and not is_final
#let show_image_captions_on_preview = data.config.preview.at("show_filenames", default: false) and not is_final
#let show_borders_on_preview = data.config.preview.at("show_borders", default: true) and not is_final
#let show_slot_info_on_preview = data.config.preview.at("show_slot_info", default: true) and not is_final

// Appendix settings
#let appendix_cfg = data.config.book.at("appendix", default: (:))
#let appendix_show = appendix_cfg.at("active", default: false)
#let appendix_nr_columns = appendix_cfg.at("columns", default: 7)
#let appendix_ref_mode = appendix_cfg.at("ref_mode", default: "positions")
#let appendix_show_page_nr_separator = appendix_cfg.at("page_separator", default: false)
#let appendix_try_strip_datetimes_from_photo_name = appendix_cfg.at("strip_timestamps", default: true)
#let appendix_label_title = appendix_cfg.at("label_title", default: "Photo Index")
#let appendix_label_page = appendix_cfg.at("label_page", default: "Page")
#let appendix_date_format = appendix_cfg.at("date_format", default: "{day}. {month} {year} {hour}:{min}")
#let appendix_date_months = appendix_cfg.at("date_months", default: (
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
))

// Image path prefix depending on output mode
#let cache_prefix = if is_final {
  ".fotobuch/cache/" + project_name + "/final/"
} else {
  ".fotobuch/cache/" + project_name + "/preview/"
}

// Page size and margins from YAML (including bleed)
#let bleed = data.config.book.bleed_mm * 1mm
#let margin = data.config.book.margin_mm * 1mm
#set page(
  width: data.config.book.page_width_mm * 1mm + 2 * bleed,
  height: data.config.book.page_height_mm * 1mm + 2 * bleed,
  margin: bleed + margin,
)

#let cover_or_none = data.config.book.at("cover", default: none)
#let has_cover = cover_or_none != none and cover_or_none.at("active", default: false)

// ── Utility functions ────────────────────────────────────────────────

// Strips an ISO date prefix (e.g. "2024-01-15T120000_") from a filename
#let try_strip_datetime_from(s) = {
  if appendix_try_strip_datetimes_from_photo_name {
    s.replace(regex("^\d{4}-\d{2}-\d{2}[@T]\d{6}[_-]?"), "")
  } else {
    s
  }
}

// Formats an ISO-8601 timestamp using the configured format string and month names
#let fmt_ts(ts, fmt, months) = {
  let s = str(ts)
  if s.len() < 16 { return s }
  let year = s.slice(0, 4)
  let day = str(int(s.slice(8, 10)))
  let month = months.at(int(s.slice(5, 7)) - 1)
  let hour = s.slice(11, 13)
  let min = s.slice(14, 16)
  fmt
    .replace("{day}", day)
    .replace("{month}", month)
    .replace("{year}", year)
    .replace("{hour}", hour)
    .replace("{min}", min)
}

// Builds a map photo_id → reference label (counter: "1","2",… / positions: "2.3")
#let calc_photo_ref() = {
  let photo_ref = (:)
  if appendix_ref_mode == "counter" {
    let n = if has_cover { 0 } else { 1 }
    for page_data in data.layout {
      for photo_id in page_data.photos {
        photo_ref.insert(photo_id, str(n))
        n = n + 1
      }
    }
  } else {
    for (pi, page_data) in data.layout.enumerate() {
      let pairs = ()
      for (i, photo_id) in page_data.photos.enumerate() {
        pairs = pairs + ((photo_id, page_data.slots.at(i, default: none)),)
      }
      for (pos, p) in pairs.enumerate() {
        photo_ref.insert(
          p.at(0),
          str(if has_cover { pi } else { pi + 1 }) + "." + str(if has_cover { pos } else { pos + 1 }),
        )
      }
    }
  }
  photo_ref
}

// Map photo_id → capture timestamp, sourced from YAML
#let photo_ts = {
  let m = (:)
  for group in data.photos {
    for file in group.files {
      m.insert(file.id, file.timestamp)
    }
  }
  m
}

// Map photo_id → area_weight, sourced from YAML
#let photo_weight = {
  let m = (:)
  for group in data.photos {
    for file in group.files {
      m.insert(file.id, file.area_weight)
    }
  }
  m
}

// ── Page rendering blocks ────────────────────────────────────────────

// Draws red bleed border and blue margin border as overlays (w_mm/h_mm = content size without bleed)
// b = bleed length value, m = margin length value
#let draw_borders(w_mm, h_mm, b, m) = [
  #place(top + left, dx: -(b / 2 + m), dy: -(b / 2 + m), rect(
    width: w_mm * 1mm + b,
    height: h_mm * 1mm + b,
    stroke: red + b,
    fill: none,
  ))
  #place(top + left, dx: -m / 2, dy: -m / 2, rect(
    width: w_mm * 1mm - m,
    height: h_mm * 1mm - m,
    stroke: (paint: blue, thickness: m),
    fill: none,
  ))
]

// AUTOBOOK: a photo is drawn at its own shape, scaled to cover its slot and clipped to it.
// autobook.yaml `crops` says where it is cut: x, y from 0 (keep the left/top edge) to 1
// (keep the right/bottom edge). Without one, `focus` (where the faces are, 0..1 of the photo)
// is kept as central as possible; without either it is centred like fit: "cover".
#let ab_crops = yaml("autobook.yaml").at("crops", default: (:))
#let ab_focus = yaml("autobook.yaml").at("focus", default: (:))
#let ab_centre_on(at, full, part) = if full > part { calc.clamp((at * full - part / 2) / (full - part), 0.0, 1.0) } else { 0.5 }
#let ab_aspect = {
  let m = (:)
  for group in data.photos {
    for file in group.files {
      m.insert(file.id, file.width_px / file.height_px)
    }
  }
  m
}
#let ab_photo(path, photo_id, w, h) = {
  let a = ab_aspect.at(photo_id, default: none)
  if a == none { return image(path, width: w, height: h, fit: "cover") }
  let (iw, ih) = if a > w / h { (h * a, h) } else { (w, w / a) }
  let (x, y) = if photo_id in ab_crops { ab_crops.at(photo_id) } else if photo_id in ab_focus {
    let (fx, fy) = ab_focus.at(photo_id)
    (ab_centre_on(fx, iw, w), ab_centre_on(fy, ih, h))
  } else { (0.5, 0.5) }
  box(width: w, height: h, clip: true,
    place(top + left, dx: -(iw - w) * x, dy: -(ih - h) * y, image(path, width: iw, height: ih)))
}

// Renders a slot: thin black frame, centered slot address, and optionally a photo with weight
#let render_slot(page_index, slot, slot_nr, photo_id, photo_ref, photo_weight) = [
  // Draw thin black frame around slot
  #place(top + left, dx: slot.x_mm * 1mm, dy: slot.y_mm * 1mm, rect(
    width: slot.width_mm * 1mm,
    height: slot.height_mm * 1mm,
    stroke: if show_slot_info_on_preview { (paint: black, thickness: 0.5pt) } else { none },
    fill: none,
  ))

  // Place image if photo_id is provided
  #if photo_id != none [
    #place(top + left, dx: slot.x_mm * 1mm, dy: slot.y_mm * 1mm,  // AUTOBOOK: crop position
      ab_photo(cache_prefix + photo_id, photo_id, slot.width_mm * 1mm, slot.height_mm * 1mm))
    #if show_image_captions_on_preview [
      #place(
        top + left,
        dx: slot.x_mm * 1mm,
        dy: (slot.y_mm + slot.height_mm / 2) * 1mm,
        text(size: 8pt, photo_id.split("/").last(), white),
      )
    ]
  ]

  // Centered slot address (always shown when show_slot_info_on_preview is true)
  #if show_slot_info_on_preview [
    #place(
      top + left,
      dx: slot.x_mm * 1mm,
      dy: slot.y_mm * 1mm,
      box(
        width: slot.width_mm * 1mm,
        height: slot.height_mm * 1mm,
        align(center + horizon, text(size: slot.width_mm * 1mm / 4, weight: "bold", fill: if photo_id == none {
          black
        } else { white })[
          #page_index:#slot_nr
          #if photo_id != none [ \(#str(calc.round(photo_weight.at(photo_id, default: 1.0), digits: 1))\)]
        ]),
      ),
    )
  ]

  // Reference badge (only if photo is present)
  #if photo_id != none and appendix_show and appendix_ref_mode == "counter" [
    #let ref_label = photo_ref.at(photo_id, default: "")
    #if ref_label != "" [
      #place(
        top + left,
        dx: (slot.x_mm + slot.width_mm - 10) * 1mm,
        dy: (slot.y_mm + slot.height_mm - 7) * 1mm,
        box(
          fill: rgb("#00000099"),
          inset: (x: 1.5mm, y: 1mm),
          text(size: 7pt, fill: white, weight: "bold")[#ref_label],
        ),
      )
    ]
  ]
]

// Renders the PREVIEW watermark diagonally across the page
#let render_preview_watermark(page_number) = [
  #place(center + horizon, rotate(-30deg, text(
    size: 120pt,
    fill: rgb("#00000055"),
    weight: "bold",
  )[PREVIEW #page_number]))
]

// Renders the photo index: page separators, group headers, entries with timestamps
#let render_appendix(photo_ref, photo_ts) = [
  #set page(margin: 10mm)
  #text(size: 22pt, weight: "bold")[#appendix_label_title]
  #v(-5mm)
  #text(size: 9pt, fill: rgb("#666666"))[
    #if appendix_ref_mode == "counter" [
    ] else [
      x.y = #appendix_label_page x, Photo y
    ]
  ]
  #v(0mm)
  #{
    let items = ()
    let cur_group = none
    for (page_nr, page_data) in data.layout.enumerate() {
      if appendix_show_page_nr_separator {
        items.push(block(
          width: 100%,
          above: 3mm,
          below: 1.5mm,
          fill: rgb("#dddddd"),
          inset: (x: 2mm, y: 1.5mm),
          text(size: 8pt, weight: "bold")[#appendix_label_page #(page_nr + 1)],
        ))
      }
      for photo_id in page_data.photos {
        let parts = photo_id.split("/")
        let group = parts.at(0)
        let name = parts.last().split(".").at(0)
        let ref_label = photo_ref.at(photo_id, default: "")
        if group != cur_group {
          cur_group = group
          items.push(block(
            width: 100%,
            above: 2mm,
            below: 2mm,
            inset: 0mm,
            text(size: 8pt, weight: "bold", fill: rgb("#333333"))[#try_strip_datetime_from(group)],
          ))
        }
        let ts = photo_ts.at(photo_id, default: none)
        items.push(block(
          width: 100%,
          above: 0pt,
          below: 1mm,
          text(size: 7pt)[
            #box(width: 6mm, inset: (x: 1mm, y: 0mm), align(left, text(fill: black, weight: "bold")[#ref_label])) #h(
              0.5mm,
            )
            #fmt_ts(ts, appendix_date_format, appendix_date_months)
            #if ts != none [#text(fill: rgb("#888888"))[ · #try_strip_datetime_from(name)]]
          ],
        ))
      }
    }
    columns(appendix_nr_columns, gutter: 4mm)[#{ for item in items { item } }]
  }
]


// ════════════════════════════════════════════════════════════════════
//  AUTOBOOK — chapter openers, page numbers, cover text.
//  Reads autobook.yaml (written by `autobook export`). Text is drawn on
//  single-photo Manual pages, keyed by Fotobuch photo id, so Fotobuch
//  itself needs no changes. Generated by tools/make_template.py.
// ════════════════════════════════════════════════════════════════════
#let ab = yaml("autobook.yaml")
#let ab_dividers = ab.at("dividers", default: (:))
#let ab_full = ab.at("fullpage", default: (:))
#let ab_pn = ab.at("page_numbers", default: (:))
#let ab_style = ab.at("style", default: (:))
#let ab_font = ab_style.at("font", default: "Libertinus Serif")
#let ab_ink = rgb(ab_style.at("ink", default: "#2d3a45"))
#let ab_accent = rgb(ab_style.at("accent", default: "#c4502a"))
#let ab_safe = ab_style.at("safe_margin_mm", default: 12) * 1mm
#let ab_trim_w = data.config.book.page_width_mm * 1mm
#let ab_trim_h = data.config.book.page_height_mm * 1mm
// text scale: 1.0 for a 210 mm page
#let ab_unit = calc.min(data.config.book.page_width_mm, data.config.book.page_height_mm) / 210.0
#set text(font: ab_font)

#let ab_single(page_data) = if page_data.photos.len() == 1 { page_data.photos.at(0) } else { none }

// Page design: the frame autobook leaves around the photos and the page colours
#let ab_frame = ab.at("frame_mm", default: 0) * 1mm
#let ab_paper = ab.at("paper", default: (bg: "#FFFFFF", ink: "#2D3A45"))
#let ab_fills = ab.at("fills", default: (:))

// Background and text colour of a page: the first photo on it with its own colour, else the book's
#let ab_paint(page_data) = {
  let p = ab_paper
  for id in page_data.photos {
    if id in ab_fills {
      p = ab_fills.at(id)
      break
    }
  }
  (bg: rgb(p.bg), ink: rgb(p.ink))
}

// The page colour under everything on the page, bleed included
#let ab_page_fill(page_data) = place(top + left, dx: -margin - bleed, dy: -margin - bleed,
  rect(width: ab_trim_w + 2 * bleed, height: ab_trim_h + 2 * bleed, stroke: none, fill: ab_paint(page_data).bg))

// Box covering the trim area (content origin is bleed + margin from the media edge)
#let ab_on_trim(body) = place(top + left, dx: -margin, dy: -margin, box(width: ab_trim_w, height: ab_trim_h, body))

// A cover photo runs on into the bleed on every side where its slot meets the cover's edge, so
// trimming (or folding around a hardcover's board) leaves no paper-coloured strip
#let ab_cover_slot(slot, total_w, h, b) = {
  let b = b / 1mm
  let near(a, z) = calc.abs(a - z) < 0.5
  let x0 = if near(slot.x_mm, 0) { -b } else { slot.x_mm }
  let y0 = if near(slot.y_mm, 0) { -b } else { slot.y_mm }
  let x1 = slot.x_mm + slot.width_mm
  let x1 = if near(x1, total_w) { total_w + b } else { x1 }
  let y1 = slot.y_mm + slot.height_mm
  let y1 = if near(y1, h) { h + b } else { y1 }
  (x_mm: x0, y_mm: y0, width_mm: x1 - x0, height_mm: y1 - y0)
}

#let ab_title_stack(info, fg) = {
  let items = ()
  items.push(par(leading: 0.35em, text(size: 36pt * ab_unit, weight: "bold", fill: fg, hyphenate: false)[#info.at("title", default: "")]))
  let sub = info.at("subtitle", default: "")
  if sub != none and sub != "" { items.push(text(size: 14pt * ab_unit, style: "italic", fill: fg)[#sub]) }
  let date = info.at("date", default: "")
  if date != none and date != "" { items.push(text(size: 9.5pt * ab_unit, tracking: 0.1em, fill: fg)[#upper(date)]) }
  stack(spacing: 3.5mm * ab_unit, ..items)
}

#let ab_divider(info, paint) = {
  let style = info.at("style", default: "hero")
  // a hero photo comes darker under the title already (autobook shades it): no transparency in the PDF
  let fg = if style == "hero" { white } else { paint.ink }
  ab_on_trim(box(width: 100%, height: 100%, inset: ab_safe,
    if style == "plain" { align(center + horizon, ab_title_stack(info, fg)) }
    else { align(left + bottom, ab_title_stack(info, fg)) }))
}

// With a frame: in the bottom frame band, in line with the outer photo edge.
// Without one: a small box in the page colour on top of the photo.
#let ab_page_number(n, paint) = {
  let right_page = calc.odd(n)
  let corner = if right_page { bottom + right } else { bottom + left }
  let num = text(size: 8pt * ab_unit, fill: paint.ink)[#n]
  if ab_frame >= 5mm {
    ab_on_trim(place(corner, dx: if right_page { -ab_frame } else { ab_frame },
      box(height: ab_frame, align(horizon, num))))
  } else {
    ab_on_trim(place(corner, dx: if right_page { -7mm } else { 7mm }, dy: -6mm,
      box(fill: paint.bg, radius: 1.5pt, inset: (x: 1.8mm, y: 1mm), num)))
  }
}

// Inner page overlay: opener text, then the page number unless it is a full page (opener, padding)
#let ab_overlay(page_data, display_nr) = {
  let pid = ab_single(page_data)
  let paint = ab_paint(page_data)
  let is_full = pid != none and (pid in ab_dividers or pid in ab_full)
  if pid != none and pid in ab_dividers { ab_divider(ab_dividers.at(pid), paint) }
  if ab_pn.at("enabled", default: true) and not is_full { ab_page_number(display_nr, paint) }
}

// Cover text: title block on the front panel, a small line on the back panel
#let ab_cover_text(spine_x, spine_w, total_w, h, c_bleed, c_margin) = {
  let c = ab.at("cover", default: (:))
  let front_x = (spine_x + spine_w) * 1mm
  let front_w = total_w * 1mm - front_x
  // scrim: the front photo is darker at the top under the white title (shaded by autobook)
  let fg = if c.at("scrim", default: true) { white } else { ab_ink }
  place(top + left, dx: front_x - c_margin, dy: -c_margin,
    box(width: front_w, height: h * 1mm, inset: (x: ab_safe + 4mm, y: ab_safe + 6mm),
      align(center + top, {
        let items = (par(leading: 0.3em, text(size: 40pt * ab_unit, weight: "bold", fill: fg, hyphenate: false)[#c.at("title", default: "")]),)
        let sub = c.at("subtitle", default: "")
        if sub != none and sub != "" { items.push(text(size: 14pt * ab_unit, style: "italic", fill: fg)[#sub]) }
        items.push(text(size: 10pt * ab_unit, tracking: 0.12em, fill: fg)[#upper(c.at("dates", default: ""))])
        stack(spacing: 4mm * ab_unit, ..items)
      })))
  let back = c.at("back_text", default: "")
  if back != "" {
    place(top + left, dx: -c_margin, dy: -c_margin,
      box(width: spine_x * 1mm, height: h * 1mm, inset: ab_safe,
        align(center + bottom, text(size: 9pt * ab_unit, fill: ab_ink)[#back])))
  }
}

// ════════════════════════════════════════════════════════════════════
//  MAIN — render pages
// ════════════════════════════════════════════════════════════════════

#let photo_ref = calc_photo_ref()

// ── Cover page ───────────────────────────────────────────────────────
#let inner_page_count = if has_cover { data.layout.len() - 1 } else { data.layout.len() }
#let cover_front_back_w = if has_cover { cover_or_none.front_back_width_mm } else { 0.0 }
#let cover_h = if has_cover { cover_or_none.height_mm } else { 0.0 }
#let cover_bleed = if has_cover { cover_or_none.bleed_mm * 1mm } else { 0mm }
#let cover_margin = if has_cover { cover_or_none.margin_mm * 1mm } else { 0mm }
#let spine_mode = if has_cover { cover_or_none.at("spine_mode", default: "auto") } else { "auto" }
#let spine_w = if has_cover {
  if spine_mode == "auto" {
    float(inner_page_count) / 10.0 * cover_or_none.spine_mm_per_10_pages
  } else {
    cover_or_none.spine_width_mm
  }
} else { 0.0 }
#let cover_total_w = if has_cover {
  if spine_mode == "auto" {
    cover_front_back_w + spine_w
  } else {
    cover_front_back_w
  }
} else { 0.0 }
// AUTOBOOK: spine x matching the cover solver (auto: starts at fb/2; fixed: centred)
#let ab_spine_x = if spine_mode == "auto" { cover_front_back_w / 2 } else { cover_front_back_w / 2 - spine_w / 2 }
#let spine_text_content = if has_cover { cover_or_none.at("spine_text", default: data.config.book.title) } else { "" }

#if has_cover [
  #[
    #set page(
      width: cover_total_w * 1mm + 2 * cover_bleed,
      height: cover_h * 1mm + 2 * cover_bleed,
      margin: cover_bleed + cover_margin,
    )
    #let cover_data = data.layout.at(0)
    #if show_borders_on_preview [
      #draw_borders(cover_total_w, cover_h, cover_bleed, cover_margin)
      // Spine area markers: two vertical green lines bounding the spine
      #place(top + left, dx: ab_spine_x * 1mm, dy: -cover_bleed, rect(
        width: spine_w * 1mm,
        height: cover_h * 1mm + 2 * cover_bleed,
        stroke: (left: green + 0.5pt, right: green + 0.5pt, top: none, bottom: none),
        fill: rgb(0, 200, 0, 20),
      ))
    ]
    #for (i, slot) in cover_data.slots.enumerate() [
      #let photo_id = cover_data.photos.at(i, default: none)
      #render_slot(0, ab_cover_slot(slot, cover_total_w, cover_h, cover_bleed), i, photo_id, photo_ref, photo_weight)  // AUTOBOOK
    ]
    // Spine text — reads bottom-to-top; dx = half of front+back = single page width
    #place(top + left, dx: ab_spine_x * 1mm, dy: 0mm, box(
      width: spine_w * 1mm,
      height: cover_h * 1mm,
      align(horizon + center, rotate(-90deg, box(
        stroke: if is_final or not show_borders_on_preview { none } else { green },
        width: cover_h * 1mm,
        align(left, text(
          size: calc.min(20mm, spine_w * 0.9 * 1mm),
          h(0.05 * cover_h * 1mm) + spine_text_content,
        )),
      ))),
    ))
    #ab_cover_text(ab_spine_x, spine_w, cover_total_w, cover_h, cover_bleed, cover_margin)  // AUTOBOOK
    #if not is_final and show_preview_watermark [#render_preview_watermark("Cover")]
    #pagebreak()
  ]
]

// ── Inner pages ──────────────────────────────────────────────────────
#let layout_start = if has_cover { 1 } else { 0 }

#for page_index in range(layout_start, data.layout.len()) [
  #let page_data = data.layout.at(page_index)
  #ab_page_fill(page_data)  // AUTOBOOK
  #if show_borders_on_preview [#draw_borders(
    data.config.book.page_width_mm,
    data.config.book.page_height_mm,
    bleed,
    margin,
  )]

  #for (i, slot) in page_data.slots.enumerate() [
    #let photo_id = page_data.photos.at(i, default: none)
    #render_slot(page_index, slot, i, photo_id, photo_ref, photo_weight)
  ]

  #let display_nr = page_index - layout_start + 1
  #ab_overlay(page_data, display_nr)  // AUTOBOOK
  #if not is_final and show_preview_watermark [#render_preview_watermark(display_nr)]

  #if page_index < data.layout.len() - 1 or appendix_show [#pagebreak()]
]

#if appendix_show [#render_appendix(photo_ref, photo_ts)]
