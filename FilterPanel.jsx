import { useState, useEffect, useRef } from "react";
import { useApp } from "../context/AppContext";

const THEME = "#379B91";
const THEME_LIGHT = "#e8f5f4";

export default function FilterPanel({ onClose }) {
  const {
    API_BASE,
    clusterFilter,
    setClusterFilter,
    branchFilter,
    setBranchFilter,
    specialityFilter,
    setSpecialityFilter,
    filterOptions,
    selectedDoctor,
    setSelectedDoctor,
  } = useApp();

  const [doctors, setDoctors] = useState([]);
  const [loadingDoctors, setLoadingDoctors] = useState(false);
  const [search, setSearch] = useState("");
  const panelRef = useRef(null);

  // Close on outside click
  useEffect(() => {
    function handle(e) {
      if (panelRef.current && !panelRef.current.contains(e.target)) {
        onClose?.();
      }
    }
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [onClose]);

  // Fetch doctors whenever any filter changes
  useEffect(() => {
    if (!clusterFilter && !branchFilter && !specialityFilter) {
      setDoctors([]);
      return;
    }
    setLoadingDoctors(true);
    const params = new URLSearchParams({ pageSize: 200 });
    if (clusterFilter) params.set("cluster", clusterFilter);
    if (branchFilter) params.set("branch", branchFilter);
    if (specialityFilter) params.set("speciality", specialityFilter);

    fetch(`${API_BASE}/doctors?${params}`)
      .then((r) => r.json())
      .then((d) => setDoctors(d.doctors || []))
      .catch(console.error)
      .finally(() => setLoadingDoctors(false));
  }, [clusterFilter, branchFilter, specialityFilter, API_BASE]);

  const anyFilterActive = clusterFilter || branchFilter || specialityFilter;

  const filtered = doctors.filter((d) => {
    const q = search.toLowerCase();
    return (
      (d.name || "").toLowerCase().includes(q) ||
      (d.business_name || "").toLowerCase().includes(q) ||
      (d.Branch || "").toLowerCase().includes(q)
    );
  });

  function clearAll() {
    setClusterFilter("");
    setBranchFilter("");
    setSpecialityFilter("");
    setDoctors([]);
    setSearch("");
  }

  return (
    <div
      ref={panelRef}
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        width: 380,
        height: "100vh",
        background: "#fff",
        boxShadow: "-4px 0 24px rgba(0,0,0,0.12)",
        zIndex: 1000,
        display: "flex",
        flexDirection: "column",
        fontFamily: "'DM Sans', 'Segoe UI', sans-serif",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "20px 24px",
          borderBottom: "1px solid #f0f0f0",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          background: THEME,
          color: "#fff",
        }}
      >
        <div>
          <div style={{ fontWeight: 800, fontSize: 16 }}>Filter & Select Doctor</div>
          <div style={{ fontSize: 11, opacity: 0.8, marginTop: 2 }}>
            Filter by Cluster, Branch &amp; Speciality
          </div>
        </div>
        <button
          onClick={onClose}
          style={{
            background: "rgba(255,255,255,0.15)",
            border: "none",
            borderRadius: 8,
            color: "#fff",
            width: 32,
            height: 32,
            cursor: "pointer",
            fontSize: 18,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          ×
        </button>
      </div>

      {/* Filter Dropdowns */}
      <div style={{ padding: "16px 24px", borderBottom: "1px solid #f0f0f0", display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: "#9ca3af", marginBottom: 2, letterSpacing: 0.5 }}>
          FILTER BY
        </div>

        {/* Cluster */}
        <div style={{ position: "relative" }}>
          <label style={{ fontSize: 11, fontWeight: 600, color: "#6b7280", display: "block", marginBottom: 4 }}>
            🏥 Cluster
          </label>
          <select
            value={clusterFilter}
            onChange={(e) => {
              setClusterFilter(e.target.value);
              setBranchFilter("");
              setSpecialityFilter("");
            }}
            style={{
              width: "100%",
              padding: "9px 32px 9px 12px",
              border: `1px solid ${clusterFilter ? THEME : "#e5e7eb"}`,
              borderRadius: 9,
              fontSize: 13,
              color: clusterFilter ? "#111827" : "#9ca3af",
              background: clusterFilter ? THEME_LIGHT : "#f9fafb",
              appearance: "none",
              cursor: "pointer",
              outline: "none",
              fontFamily: "inherit",
              fontWeight: clusterFilter ? 600 : 400,
            }}
          >
            <option value="">— Select Cluster —</option>
            {filterOptions.clusters.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <svg style={{ position: "absolute", right: 10, top: "calc(50% + 10px)", transform: "translateY(-50%)", pointerEvents: "none" }}
            width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="#6b7280" strokeWidth={2}>
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </div>

        {/* Branch */}
        <div style={{ position: "relative" }}>
          <label style={{ fontSize: 11, fontWeight: 600, color: "#6b7280", display: "block", marginBottom: 4 }}>
            📍 Branch
          </label>
          <select
            value={branchFilter}
            onChange={(e) => {
              setBranchFilter(e.target.value);
              setSpecialityFilter("");
            }}
            style={{
              width: "100%",
              padding: "9px 32px 9px 12px",
              border: `1px solid ${branchFilter ? THEME : "#e5e7eb"}`,
              borderRadius: 9,
              fontSize: 13,
              color: branchFilter ? "#111827" : "#9ca3af",
              background: branchFilter ? THEME_LIGHT : "#f9fafb",
              appearance: "none",
              cursor: "pointer",
              outline: "none",
              fontFamily: "inherit",
              fontWeight: branchFilter ? 600 : 400,
            }}
          >
            <option value="">— Select Branch —</option>
            {filterOptions.locations.map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
          <svg style={{ position: "absolute", right: 10, top: "calc(50% + 10px)", transform: "translateY(-50%)", pointerEvents: "none" }}
            width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="#6b7280" strokeWidth={2}>
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </div>

        {/* Speciality */}
        <div style={{ position: "relative" }}>
          <label style={{ fontSize: 11, fontWeight: 600, color: "#6b7280", display: "block", marginBottom: 4 }}>
            🩺 Speciality
          </label>
          <select
            value={specialityFilter}
            onChange={(e) => setSpecialityFilter(e.target.value)}
            style={{
              width: "100%",
              padding: "9px 32px 9px 12px",
              border: `1px solid ${specialityFilter ? THEME : "#e5e7eb"}`,
              borderRadius: 9,
              fontSize: 13,
              color: specialityFilter ? "#111827" : "#9ca3af",
              background: specialityFilter ? THEME_LIGHT : "#f9fafb",
              appearance: "none",
              cursor: "pointer",
              outline: "none",
              fontFamily: "inherit",
              fontWeight: specialityFilter ? 600 : 400,
            }}
          >
            <option value="">— Select Speciality —</option>
            {filterOptions.specialities.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <svg style={{ position: "absolute", right: 10, top: "calc(50% + 10px)", transform: "translateY(-50%)", pointerEvents: "none" }}
            width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="#6b7280" strokeWidth={2}>
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </div>

        {anyFilterActive && (
          <button onClick={clearAll}
            style={{ padding: "7px 14px", borderRadius: 8, border: "1px solid #e5e7eb", background: "#fff", fontSize: 11, cursor: "pointer", color: "#6b7280", alignSelf: "flex-start" }}>
            ✕ Clear All Filters
          </button>
        )}
      </div>

      {/* Doctor List */}
      <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        {anyFilterActive && (
          <>
            <div style={{ padding: "12px 24px", borderBottom: "1px solid #f0f0f0" }}>
              <div style={{ position: "relative" }}>
                <svg
                  style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)" }}
                  width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="#9ca3af" strokeWidth={2}
                >
                  <circle cx={11} cy={11} r={8} /><line x1={21} y1={21} x2={16.65} y2={16.65} />
                </svg>
                <input
                  type="text"
                  placeholder="Search doctors..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  style={{
                    width: "100%",
                    padding: "8px 10px 8px 32px",
                    border: "1px solid #e5e7eb",
                    borderRadius: 8,
                    fontSize: 13,
                    outline: "none",
                    boxSizing: "border-box",
                    fontFamily: "inherit",
                  }}
                />
              </div>
              <div style={{ marginTop: 8, fontSize: 11, color: "#9ca3af" }}>
                {loadingDoctors ? "Loading..." : `${filtered.length} doctor(s) found`}
              </div>
            </div>

            <div style={{ flex: 1, overflowY: "auto", padding: "8px 12px" }}>
              {loadingDoctors && (
                <div style={{ textAlign: "center", padding: 32, color: "#9ca3af" }}>
                  <div style={{ fontSize: 24, marginBottom: 8 }}>⏳</div>
                  Loading doctors...
                </div>
              )}
              {!loadingDoctors && filtered.length === 0 && (
                <div style={{ textAlign: "center", padding: 32, color: "#9ca3af", fontSize: 13 }}>
                  No doctors found for the selected filters.
                </div>
              )}
              {filtered.map((doc) => {
                const isSelected = selectedDoctor?._id === doc._id;
                return (
                  <button
                    key={doc._id}
                    onClick={() => { setSelectedDoctor(doc); onClose?.(); }}
                    style={{
                      width: "100%",
                      padding: "12px 14px",
                      marginBottom: 6,
                      borderRadius: 12,
                      border: isSelected ? `2px solid ${THEME}` : "1px solid #f0f0f0",
                      background: isSelected ? THEME_LIGHT : "#fff",
                      cursor: "pointer",
                      textAlign: "left",
                      display: "flex",
                      flexDirection: "column",
                      gap: 4,
                      boxSizing: "border-box",
                    }}
                  >
                    <div style={{ fontWeight: 700, fontSize: 13, color: "#111827" }}>
                      {isSelected && <span style={{ color: THEME }}>✓ </span>}
                      {doc.name || doc.business_name}
                    </div>
                    <div style={{ fontSize: 11, color: "#6b7280" }}>
                      {doc.primaryCategory}
                    </div>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 2 }}>
                      {doc.Cluster && (
                        <span style={{ fontSize: 10, background: "#f0fdf4", color: "#166534", padding: "2px 7px", borderRadius: 4, fontWeight: 600 }}>
                          {doc.Cluster}
                        </span>
                      )}
                      {doc.Branch && (
                        <span style={{ fontSize: 10, background: "#eff6ff", color: "#1e40af", padding: "2px 7px", borderRadius: 4, fontWeight: 600 }}>
                          {doc.Branch}
                        </span>
                      )}
                      {doc.averageRating && (
                        <span style={{ fontSize: 10, background: "#fefce8", color: "#92400e", padding: "2px 7px", borderRadius: 4, fontWeight: 600 }}>
                          ★ {doc.averageRating}
                        </span>
                      )}
                    </div>
                    {doc.address && (
                      <div style={{ fontSize: 10, color: "#9ca3af", marginTop: 2, lineHeight: 1.4 }}>
                        {String(doc.address).slice(0, 80)}{doc.address?.length > 80 ? "…" : ""}
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          </>
        )}

        {!anyFilterActive && (
          <div style={{ textAlign: "center", padding: 48, color: "#9ca3af" }}>
            <div style={{ fontSize: 40, marginBottom: 12 }}>🔍</div>
            <div style={{ fontWeight: 600, fontSize: 14, color: "#374151", marginBottom: 4 }}>
              Select a filter above
            </div>
            <div style={{ fontSize: 12 }}>
              Choose a Cluster, Branch, or Speciality to load doctors
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
