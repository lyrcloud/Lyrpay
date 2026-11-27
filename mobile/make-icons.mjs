// Renders the launcher icon and splash artwork (assets/*.png) from the website's favicon.svg for `npm run assets`.
import { mkdirSync, readFileSync } from "node:fs";
import sharp from "sharp";

mkdirSync("assets", { recursive: true });
const svg = readFileSync("../frontend/public/favicon.svg", "utf8");
// The mark alone (white strokes), for the adaptive icon's foreground over the brand gradient
const mark = svg.replace(/<rect[^>]*\/>/, "").replace(/<path d="M4 22[^>]*\/>/, "");
const gradient = svg.replace(/<path[\s\S]*<\/svg>/, "</svg>").replace('rx="18"', 'rx="0"');

const png = (source, size) => sharp(Buffer.from(source), { density: 1200 }).resize(size, size).png();
// Full square icon: Android applies its own mask
await png(svg.replace('rx="18"', 'rx="0"'), 1024).toFile("assets/icon-only.png");
// Adaptive icon: the mark shrunk into the 66% safe zone, on the gradient layer
const markPng = await png(mark, 560).toBuffer();
await sharp({ create: { width: 1024, height: 1024, channels: 4, background: { r: 0, g: 0, b: 0, alpha: 0 } } })
  .composite([{ input: markPng, gravity: "center" }]).png().toFile("assets/icon-foreground.png");
await png(gradient, 1024).toFile("assets/icon-background.png");
// Splash: the rounded icon centred on the site's background
const icon = await png(svg, 520).toBuffer();
await sharp({ create: { width: 2732, height: 2732, channels: 4, background: "#f5f6fb" } })
  .composite([{ input: icon, gravity: "center" }]).png().toFile("assets/splash.png");
console.log("icons written");
