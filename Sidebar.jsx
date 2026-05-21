import { useNavigate, useLocation } from 'react-router-dom';

const THEME = "#379B91";
const DARK_BG = "#0f1f38";
const DARK_MID = "#162b4a";

const navItems = [
  { emoji: "⊞", label: "Dashboard",             path: "/dashboard" },
  { emoji: "⊡", label: "Personalized Dashboard", path: "/personalized-dashboard" },
  { emoji: "💬", label: "Review Hub",             path: "/review-hub" },
  { emoji: "📊", label: "Analytics",              path: "/analytics" },
];

export default function Sidebar() {
  const navigate    = useNavigate();
  const { pathname } = useLocation();

  return (
    <aside style={{
      width: 230, minWidth: 230, background: DARK_BG,
      display: "flex", flexDirection: "column",
      padding: "20px 14px 20px", flexShrink: 0,
      boxSizing: "border-box", overflow: "hidden",
    }}>
      {/* ── Logo ── */}
      <div style={{ paddingBottom: 18, marginBottom: 8, borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
        <img
          src="https://multipliersolutions.in/manipalhospitals/manipallogo2.png"
          alt="Manipal Hospitals"
          style={{ width: "100%", maxWidth: 170, height: "auto", objectFit: "contain", display: "block" }}
          onError={e => {
            // fallback text logo if image fails
            e.currentTarget.style.display = "none";
            e.currentTarget.nextSibling.style.display = "block";
          }}
        />
        <div style={{ display: "none" }}>
          <div style={{ display: "flex", alignItems: "flex-end" }}>
            <span style={{ fontFamily: "'Georgia',serif", fontSize: 20, color: "#5bc4c0" }}>manipal</span>
            <span style={{ fontFamily: "'Georgia',serif", fontSize: 20, color: "#1a56db" }}>hospitals</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 2 }}>
            <span style={{ fontSize: 9, color: "#e53e3e", letterSpacing: 1, fontWeight: 600 }}>LIFE'S ON</span>
            <span style={{ width: 6, height: 6, background: "#e53e3e", borderRadius: 1, display: "inline-block" }}/>
          </div>
        </div>
      </div>

      {/* ── Nav ── */}
      <nav style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1, marginTop: 8 }}>
        {navItems.map(n => {
          const active = pathname === n.path ||
            (n.path !== "/" && pathname.startsWith(n.path + "/"));
          return (
            <button key={n.label} onClick={() => navigate(n.path)}
              style={{
                display: "flex", alignItems: "center", gap: 10,
                padding: "10px 14px", borderRadius: 10, border: "none",
                cursor: "pointer", width: "100%", boxSizing: "border-box",
                background: active ? "rgba(55,155,145,0.18)" : "transparent",
                color: active ? "#5bc4c0" : "rgba(255,255,255,0.5)",
                fontWeight: active ? 700 : 400, fontSize: 13, textAlign: "left",
                borderLeft: active ? `3px solid ${THEME}` : "3px solid transparent",
                transition: "all .15s",
              }}>
              <span style={{ fontSize: 15 }}>{n.emoji}</span>
              {n.label}
            </button>
          );
        })}
      </nav>

      {/* ── Bottom ── */}
      <div style={{ borderTop: "1px solid rgba(255,255,255,0.07)", paddingTop: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 12, paddingLeft: 4 }}>
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#22c55e", display: "inline-block", boxShadow: "0 0 6px #22c55e" }}/>
          <span style={{ fontSize: 9, color: "rgba(255,255,255,0.35)", letterSpacing: 0.5 }}>SYSTEM LIVE • 122MS</span>
        </div>
        <button onClick={() => navigate("/review-hub")}
          style={{
            width: "100%", padding: "11px 14px",
            background: `linear-gradient(135deg,${THEME},#2a7a71)`,
            color: "#fff", border: "none", borderRadius: 10,
            fontWeight: 700, fontSize: 13, cursor: "pointer",
            boxShadow: "0 4px 12px rgba(55,155,145,0.35)",
          }}>
          + New Response
        </button>
      </div>
    </aside>
  );
}
