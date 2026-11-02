// Title pictures, as titles.py: a title's text (and background) drawn into a canvas the size of the video,
// straight alpha. Fonts are font families the phone has, not font files.

export const POSITIONS = ["upper", "center", "lower"];
export const BACKGROUNDS = ["none", "colour", "image"];
export const FONTS = [
  ["", "Built-in"], ["system-ui, sans-serif", "System"], ["Georgia, serif", "Serif"],
  ["'Courier New', monospace", "Typewriter"], ["Impact, 'Arial Black', sans-serif", "Impact"],
  ["'Trebuchet MS', sans-serif", "Trebuchet"], ["'Brush Script MT', cursive", "Script"],
];

const family = font => {
  if (!font) return "'Helvetica Neue', Arial, sans-serif";
  if (FONTS.some(([f]) => f === font)) return font;
  // A font file name from the desktop: try its family name, then the built-in.
  const name = font.replace(/\.(ttf|otf)$/i, "").replace(/[-_](Regular|Bold|Italic|Light|Medium)$/i, "");
  return `'${name}', 'Helvetica Neue', Arial, sans-serif`;
};

function wrap(ctx, text, width) {
  const lines = [];
  for (const para of text.split("\n")) {
    let line = "";
    for (const w of para.split(" ")) {
      const trial = line ? `${line} ${w}` : w;
      if (line && ctx.measureText(trial).width > width) {
        lines.push(line);
        line = w;
      } else line = trial;
    }
    lines.push(line);
  }
  return lines;
}

// The part of a sw x sh picture that covers width x height, zoomed in by zoom, centred at (cx, cy).
export function coverBox(sw, sh, width, height, zoom = 1, cx = 0.5, cy = 0.5) {
  const aspect = width / height;
  let [bw, bh] = sw / sh < aspect ? [sw, sw / aspect] : [sh * aspect, sh];
  bw = Math.min(bw / zoom, sw);
  bh = Math.min(bh / zoom, sh);
  const x0 = Math.max(0, Math.min(cx * sw - bw / 2, sw - bw));
  const y0 = Math.max(0, Math.min(cy * sh - bh / 2, sh - bh));
  return [x0, y0, bw, bh];
}

// A canvas (width x height) with the title drawn; image: an ImageBitmap for an "image" background, or null.
// card: the title has its own time, so it always gets a background (black when it has none).
export function renderTitle(title, width, height, image = null, card = false) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  let background = title.background;
  if (background === "image") {
    if (image) {
      const [x, y, w, h] = coverBox(image.width, image.height, width, height);
      ctx.drawImage(image, x, y, w, h, 0, 0, width, height);
    } else background = "colour";
  }
  if (background === "colour" || (card && background === "none")) {
    ctx.fillStyle = background === "colour" ? title.colour : "#000000";
    ctx.fillRect(0, 0, width, height);
  }
  // Sized by the short side, so a title looks the same in a vertical video as in a wide one.
  const mainPx = Math.max(8, Math.trunc(title.size * Math.min(width, height)));
  const subPx = Math.max(8, Math.trunc(mainPx * 0.45));
  const fam = family(title.font);
  const blocks = [];
  ctx.font = `${mainPx}px ${fam}`;
  if (title.text.trim()) for (const l of wrap(ctx, title.text, width * 0.88)) blocks.push([l, mainPx]);
  ctx.font = `${subPx}px ${fam}`;
  if (title.subtitle.trim()) for (const l of wrap(ctx, title.subtitle, width * 0.88)) blocks.push([l, subPx]);
  if (!blocks.length) return canvas;
  const heights = blocks.map(([, px]) => Math.trunc(px * 1.15));
  const gaps = blocks.map(([, px]) => Math.trunc(px * 0.25));
  const total = heights.reduce((a, b) => a + b, 0) + gaps.slice(0, -1).reduce((a, b) => a + b, 0);
  const centre = ({ upper: 0.24, center: 0.5, lower: 0.76 }[title.position] ?? 0.5) * height;
  let y = Math.max(0, Math.min(centre - total / 2, height - total));
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const placed = blocks.map(([line, px], k) => {
    const at = [width / 2, y + heights[k] / 2, line, px];
    y += heights[k] + gaps[k];
    return at;
  });
  for (const [x, yy, line, px] of placed) {
    ctx.font = `${px}px ${fam}`;
    if (title.shadow) {
      ctx.shadowColor = "rgba(0,0,0,0.75)";
      ctx.shadowBlur = Math.max(1, mainPx / 8);
      ctx.shadowOffsetX = ctx.shadowOffsetY = Math.max(1, Math.trunc(mainPx / 24));
    }
    ctx.fillStyle = title.text_colour;
    ctx.fillText(line, x, yy);
  }
  return canvas;
}
