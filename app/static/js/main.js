// main.js — NF Manager (GClix-inspired UI)

// ── Auto-dismiss alerts ──
document.querySelectorAll('.alert').forEach(el => {
  setTimeout(() => {
    el.style.transition = 'opacity 0.6s';
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 600);
  }, 4500);
});

// ── Mobile nav drawer ──
const hamburger = document.getElementById('navHamburger');
const drawer    = document.getElementById('mobileDrawer');

if (hamburger && drawer) {
  hamburger.addEventListener('click', () => {
    drawer.classList.toggle('open');
    const isOpen = drawer.classList.contains('open');
    hamburger.innerHTML = isOpen
      ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`
      : `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/></svg>`;
  });
  // Close drawer when a link inside is clicked
  drawer.querySelectorAll('a').forEach(link => {
    link.addEventListener('click', () => {
      drawer.classList.remove('open');
    });
  });
}

// ── Nav scroll effect ──
const nav = document.getElementById('mainNav');
if (nav) {
  window.addEventListener('scroll', () => {
    if (window.scrollY > 20) {
      nav.style.borderBottomColor = 'rgba(255,255,255,0.1)';
    } else {
      nav.style.borderBottomColor = 'rgba(255,255,255,0.08)';
    }
  }, { passive: true });
}

// ── Copy helper (global) ──
function copyText(text, label) {
  navigator.clipboard.writeText(text).then(() => {
    showToast('✅ ' + (label || 'Copied!'));
  }).catch(() => {
    alert('Copy failed. Please copy manually.');
  });
}

// ── Toast notification ──
function showToast(msg, duration = 2800) {
  const existing = document.getElementById('__toast');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.id = '__toast';
  toast.style.cssText = `
    position:fixed; bottom:28px; right:28px; z-index:9999;
    background:rgba(20,20,20,0.95); border:1px solid rgba(255,255,255,0.12);
    color:#f1f1f1; padding:12px 20px; border-radius:10px;
    font-size:13px; font-weight:600; font-family:'Inter',sans-serif;
    box-shadow:0 8px 32px rgba(0,0,0,0.5);
    animation:slideUp 0.3s cubic-bezier(0.16,1,0.3,1) both;
    backdrop-filter:blur(12px);
  `;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.style.transition = 'opacity 0.4s';
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 400);
  }, duration);
}
