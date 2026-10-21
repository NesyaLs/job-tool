/* Fabrique de DOM. Aucune dépendance : la brique la plus basse du front.

   Deux règles tenues ici plutôt que par la vigilance de chaque écran :
   - aucun style en ligne : `style` est refusé, tout passe par des classes ;
   - aucun `innerHTML` : le texte est toujours inséré comme texte. */

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  applyProps(node, props);
  append(node, children);
  return node;
}

function applyProps(node, props) {
  for (const key in props) {
    const value = props[key];
    if (value == null || value === false) continue;
    if (key === 'style') throw new Error('style en ligne refusé : utiliser une classe CSS');
    if (key === 'class') node.className = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'value' || key === 'checked') node[key] = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
}

function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.appendChild(typeof child === 'string' || typeof child === 'number'
      ? document.createTextNode(String(child))
      : child);
  }
}

/* Remplace le contenu d'un nœud (ignore les enfants absents). */
export function fill(node, ...children) {
  node.replaceChildren();
  append(node, children);
  return node;
}

/* ---------- Icônes (SVG construit nœud par nœud, jamais par chaîne HTML) ---------- */

const SVG_NS = 'http://www.w3.org/2000/svg';

const ICONS = {
  search: [['circle', { cx: 11, cy: 11, r: 7 }], ['path', { d: 'M20 20l-4-4' }]],
  hide: [['path', { d: 'M3 12s3.5-6 9-6 9 6 9 6-3.5 6-9 6-9-6-9-6z' }], ['path', { d: 'M4 4l16 16' }]],
  show: [['path', { d: 'M3 12s3.5-6 9-6 9 6 9 6-3.5 6-9 6-9-6-9-6z' }], ['circle', { cx: 12, cy: 12, r: 2.5 }]],
  heart: [['path', { d: 'M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z' }]],
  arrow: [['path', { d: 'M5 12h14M13 6l6 6-6 6' }]],
  back: [['path', { d: 'M19 12H5M11 6l-6 6 6 6' }]],
  close: [['path', { d: 'M6 6l12 12M18 6L6 18' }]],
  menu: [['path', { d: 'M4 7h16M4 12h16M4 17h16' }]],
  plus: [['path', { d: 'M12 5v14M5 12h14' }]],
  sparkle: [['path', { d: 'M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z' }]],
  download: [['path', { d: 'M12 4v11M7 10l5 5 5-5M5 20h14' }]],
  refresh: [['path', { d: 'M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6' }]],
  edit: [['path', { d: 'M4 20h4L19 9l-4-4L4 16v4z' }]],
  more: [['circle', { cx: 5, cy: 12, r: 1.3 }], ['circle', { cx: 12, cy: 12, r: 1.3 }], ['circle', { cx: 19, cy: 12, r: 1.3 }]],
  check: [['path', { d: 'M5 12.5l4.5 4.5L19 7.5' }]],
  cross: [['path', { d: 'M7 7l10 10M17 7L7 17' }]],
  logout: [['path', { d: 'M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10' }]],
  shield: [['path', { d: 'M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z' }]],
  journal: [['path', { d: 'M6 3h11a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H6zM9 8h6M9 12h6M9 16h4' }]],
  settings: [['circle', { cx: 12, cy: 12, r: 3 }], ['path', { d: 'M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1' }]],
  copy: [['path', { d: 'M9 9h10v10H9zM5 15V5h10' }]],
  external: [['path', { d: 'M14 5h5v5M19 5l-8 8M17 14v5H5V7h5' }]],
  undo: [['path', { d: 'M9 7L4 12l5 5M4 12h11a5 5 0 0 1 0 10h-2' }]],
  keyboard: [['path', { d: 'M3 7h18v10H3zM7 11h1M11 11h1M15 11h1M8 14h8' }]],
  lightning: [['path', { d: 'M13 3L5 13h6l-1 8 8-10h-6z' }]],
  chevron: [['path', { d: 'M8 10l4 4 4-4' }]],
};

/* Icône décorative : cachée des lecteurs d'écran, c'est le libellé voisin qui parle. */
export function icon(name) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  for (const [tag, attrs] of ICONS[name] || []) {
    const shape = document.createElementNS(SVG_NS, tag);
    for (const key in attrs) shape.setAttribute(key, String(attrs[key]));
    svg.appendChild(shape);
  }
  return svg;
}

/* Deux lettres pour l'avatar, tirées de l'e-mail (ou d'un nom). */
export function initialsOf(text) {
  if (!text) return 'JT';
  const name = text.split('@')[0];
  const parts = name.split(/[\s._-]+/).filter(Boolean);
  const letters = parts.length > 1 ? parts[0][0] + parts[1][0] : name.slice(0, 2);
  return letters.toUpperCase();
}

/* Identifiant court pour relier un libellé à son champ, ou une nouvelle ligne locale. */
export function uid(prefix = 'u') {
  return prefix + Math.random().toString(36).slice(2, 9);
}
