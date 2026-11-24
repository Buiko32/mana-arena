(() => {
  'use strict';
  window.renderPlayerAvatar = ({photo_url, avatar_hue, avatar_color}, className = 'avatar') => {
    const hue = Number.isInteger(avatar_hue) && avatar_hue >= 0 && avatar_hue <= 360 ? avatar_hue : 215;
    const color = /^#[0-9a-f]{6}$/i.test(avatar_color || '') ? avatar_color : '';
    const photo = /^\/photos\/[a-f0-9-]+\.jpg$/i.test(photo_url || '') ? photo_url : '';
    return `<span class="${className}" aria-hidden="true" style="--avatar-hue:${hue}${color ? `;--avatar-color:${color}` : ''}"><svg class="avatar-mascot" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="32" fill="#172638"/><path d="M8 64c1-16 10-24 24-24s23 8 24 24" fill="var(--avatar-color,hsl(var(--avatar-hue) 62% 47%))"/><path d="M13 34C13 16 21 6 32 6s19 10 19 28l-6 13H19z" fill="var(--avatar-color,hsl(var(--avatar-hue) 64% 48%))"/><path d="M17 27C18 16 23 11 32 11s14 5 15 16c-4-4-9-6-15-6s-11 2-15 6" fill="var(--avatar-color,hsl(var(--avatar-hue) 68% 62%))"/><ellipse cx="32" cy="32" rx="14" ry="15" fill="#f3dfbe"/><circle cx="27" cy="31" r="2" fill="#263341"/><circle cx="37" cy="31" r="2" fill="#263341"/><path d="M28 37q4 3 8 0" fill="none" stroke="#966c61" stroke-width="1.8" stroke-linecap="round"/><path d="M24 47l8 8 8-8-8-3z" fill="#e7b85d"/><path d="M32 48v4m-2-2h4" stroke="#fff1ca" stroke-width="1.2" stroke-linecap="round"/></svg>${photo ? `<img src="${photo}" alt="" loading="lazy">` : ''}</span>`;
  };
  document.addEventListener('error', event => {
    if (event.target.matches?.('.avatar img, .life-avatar img')) event.target.remove();
  }, true);
})();
