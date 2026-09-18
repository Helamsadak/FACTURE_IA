const API = "";
const $ = (id) => document.getElementById(id);

/* ===== Délégation robuste (tête de fichier : ne dépend d'aucun init) =====
   Un seul écouteur au niveau DOCUMENT attrape les clics, même si des
   listeners plus bas sont morts à cause d'un crash. Action prioritaire :
   suppression de la sélection. */
document.addEventListener("click", (ev) => {
  const cible = ev.target;
  const delBtn = cible.closest ? cible.closest("#sel-del-btn") : null;
  if (delBtn) {
    ev.preventDefault();
    supprimerSelection && supprimerSelection();
    return;
  }
  const showBtn = cible.closest ? cible.closest("#sel-show-btn") : null;
  if (showBtn) {
    ev.preventDefault();
    afficherSelection && afficherSelection();
    return;
  }
});

let username = localStorage.getItem("username") || "";

/* ===== Utilitaires ===== */
function message(texte, type = "success") {
  const el = $("auth-message");
  el.textContent = texte;
  el.className = `auth-message ${type}`;
}

function toast(texte, type = "") {
  const t = document.createElement("div");
  t.className = `toast ${type}`;
  t.textContent = texte;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

async function api(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${API}${url}`, { ...options, headers });
  if (
    res.status === 401 &&
    !url.includes("login") &&
    !url.includes("register") &&
    !url.includes("logout")
  ) {
    logout();
    throw new Error("Session expirée");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return res.json();
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function montant(v) {
  if (v == null || v === "") return null;
  const n = Number(v);
  if (!isFinite(n)) return String(v) + " TND";
  const [entier, decimales] = n.toFixed(3).split(".");
  const groupe = entier.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  return groupe + "." + decimales + " TND";
}

function valeur(v) {
  return v != null && v !== "" && String(v).trim() !== ""
    ? escapeHtml(v)
    : '<span class="empty">Non renseigné</span>';
}

/* ===== Sélection multiple de factures ===== */
let selectionFactures = new Set();

function majBoutonSelection() {
  const n = selectionFactures.size;
  const txt = `Sélection (${n})`;
  ["sel-show-btn", "sel-del-btn"].forEach((id) => {
    const b = $(id);
    if (b) b.textContent = id === "sel-show-btn" ? `Afficher la sélection (${n})` : `Supprimer la sélection (${n})`;
  });
}

function toggleSelection(id, checked) {
  if (checked) selectionFactures.add(id);
  else selectionFactures.delete(id);
  majBoutonSelection();
}

function toggleToutSelection(ids, checked) {
  selectionFactures = new Set(checked ? ids : []);
  document.querySelectorAll(".sel-check").forEach((cb) => (cb.checked = checked));
  majBoutonSelection();
}

async function afficherSelection() {
  if (!selectionFactures.size) {
    toast("Sélectionnez au moins une facture.", "error");
    return;
  }
  try {
    const data = await api("/api/factures");
    const toutes = Array.isArray(data) ? data : data.factures || [];
    const filtrees = toutes.filter((f) => selectionFactures.has(f.id));
    if (!filtrees.length) {
      toast("Aucune des factures sélectionnées n'a été trouvée.", "error");
      return;
    }
    afficherTable(filtrees);
    goPage("list-tab");
  } catch (err) {
    toast(err.message, "error");
  }
}

function reinitialiserSelection() {
  selectionFactures.clear();
  majBoutonSelection();
  document.querySelectorAll(".sel-check").forEach((cb) => (cb.checked = false));
  if ($("sel-tout")) $("sel-tout").checked = false;
  const note = $("suppliers-note");
  if (note) note.style.display = "none";
  chargerFournisseurs();
}

let suppressionEnCours = false;

async function supprimerSelection() {
  if (suppressionEnCours) return;
  const n = selectionFactures.size;
  if (!n) {
    toast("Aucune facture sélectionnée.", "error");
    return;
  }
  if (!confirm(`Supprimer définitivement les ${n} facture(s) sélectionnée(s) ?`))
    return;
  suppressionEnCours = true;
  $("sel-del-btn").disabled = true;
  toast(`Suppression de ${n} facture(s)…`, "info");
  let ok = 0;
  const ids = Array.from(selectionFactures);
  try {
    for (const id of ids) {
      const r = await api(`/api/factures/${id}`, { method: "DELETE" });
      if (r && r.detail) ok++;
    }
    toast(
      ok === n
        ? `${ok} facture(s) supprimée(s) avec succès.`
        : `${ok}/${n} supprimée(s) — le reste a échoué.`,
      ok === n ? "success" : "warning"
    );
  } catch (err) {
    toast("Erreur pendant la suppression : " + err.message, "error");
  } finally {
    suppressionEnCours = false;
    $("sel-del-btn").disabled = false;
    reinitialiserSelection();
    loadInvoices();
  }
}

/* ===== Authentification ===== */
async function logout() {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (e) {}
  username = "";
  localStorage.removeItem("username");
  reinitialiserVues();
  showAuth();
}

function reinitialiserVues() {
  $("search-input").value = "";
  $("invoices-table-wrap").innerHTML = "";
  $("suppliers-wrap").innerHTML = "";
  $("upload-result").style.display = "none";
  $("upload-result").innerHTML = "";
  $("file-chip-wrap").innerHTML = "";
  reinitialiserPipeline();
  $("modal").style.display = "none";
}

function showAuth() {
  $("app-view").style.display = "none";
  $("auth-view").style.display = "flex";
  afficherFormulaire("login-form");
  $("auth-message").textContent = "";
}

function afficherFormulaire(nom) {
  document.querySelectorAll(".auth-form").forEach((f) => (f.style.display = "none"));
  $(nom).style.display = "block";
  document.querySelectorAll(".auth-tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.form === nom);
  });
}

function showApp() {
  reinitialiserVues();
  $("auth-view").style.display = "none";
  $("app-view").style.display = "block";
  goPage("home-tab");
}

/* ===== Navigation ===== */
function goPage(nom) {
  document.querySelectorAll(".page").forEach((p) => (p.style.display = "none"));
  $(nom).style.display = "block";
  document.querySelectorAll(".nav-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.page === nom);
  });
  $("navbar-menu").classList.remove("open");

  if (nom === "home-tab") { chargerStats(); chargerDashboard(); }
  if (nom === "list-tab") loadInvoices();
  if (nom === "suppliers-tab") chargerFournisseurs();
  if (nom === "assistant-tab") setTimeout(() => $("chat-input").focus(), 150);
  if (nom === "account-tab") chargerCompte();
}

document.querySelectorAll(".nav-btn").forEach((b) =>
  b.addEventListener("click", () => goPage(b.dataset.page))
);

$("nav-toggle").addEventListener("click", () => $("navbar-menu").classList.toggle("open"));

/* ===== Login / Register / Forgot ===== */
async function avecChargement(bouton, texte, callback) {
  bouton.disabled = true;
  const original = bouton.textContent;
  bouton.textContent = texte;
  try {
    await callback();
  } finally {
    bouton.disabled = false;
    bouton.textContent = original;
  }
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const bouton = e.target.querySelector("button[type=submit]");
  await avecChargement(bouton, "Connexion…", async () => {
    message("");
    try {
      const data = await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({
          username: $("login-username").value,
          password: $("login-password").value,
        }),
      });
      username = data.username;
      localStorage.setItem("username", username);
      $("login-password").value = "";
      showApp();
    } catch (err) {
      message(err.message, "error");
    }
  });
});

$("register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const bouton = e.target.querySelector("button[type=submit]");
  await avecChargement(bouton, "Création…", async () => {
    message("");
    try {
      const password = $("reg-password").value;
      const confirmation = $("reg-password-confirm").value;
      if (password !== confirmation) {
        message("Les mots de passe ne correspondent pas", "error");
        return;
      }
      const data = await api("/api/auth/register", {
        method: "POST",
        body: JSON.stringify({
          username: $("reg-username").value,
          password,
          password_confirmation: confirmation,
          full_name: $("reg-fullname").value || null,
          phone: $("reg-phone").value || null,
          role: $("reg-role").value,
        }),
      });
      username = data.username;
      localStorage.setItem("username", username);
      // Le code de récupération n'est affiché qu'UNE SEULE fois :
      if (data.recovery_code) {
        $("recovery-code-value").textContent = data.recovery_code;
        $("recovery-modal").style.display = "flex";
      } else {
        showApp();
        toast("Compte créé, bienvenue !", "success");
      }
    } catch (err) {
      message(err.message, "error");
    }
  });
});

$("recovery-close").addEventListener("click", () => {
  $("recovery-modal").style.display = "none";
  showApp();
  toast("Compte créé, bienvenue !", "success");
});

$("recovery-copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("recovery-code-value").textContent);
    toast("Code copié !", "success");
  } catch (err) {
    toast("Copie impossible, sélectionnez le code manuellement", "error");
  }
});

$("recovery-print").addEventListener("click", () => {
  const code = $("recovery-code-value").textContent;
  const w = window.open("", "_blank");
  if (w) {
    w.document.write(
      "<style>body{font-family:system-ui;max-width:520px;margin:40px auto;padding:0 20px;color:#222}code{font-size:22px;letter-spacing:2px;background:#eef2ff;padding:10px 16px;border-radius:8px;display:inline-block}</style>" +
      "<h2>OCR Facture — code de récupération</h2>" +
      "<p>Ce code permet de réinitialiser votre mot de passe si vous l'oubliez. Conservez-le précieusement, il ne sera plus jamais affiché.</p>" +
      "<p>Compte : <b>" + escapeHtml(username) + "</b></p>" +
      "<code>" + escapeHtml(code) + "</code>" +
      "<script>window.print();<\/script>"
    );
    w.document.close();
  }
});

$("forgot-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  message("");
  try {
    const nouveau = $("forgot-new-password").value;
    const confirmation = $("forgot-new-password-confirm").value;
    if (nouveau !== confirmation) {
      message("Les mots de passe ne correspondent pas", "error");
      return;
    }
    const data = await api("/api/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({
        username: $("forgot-username").value,
        recovery_code: $("forgot-code").value,
        new_password: nouveau,
        new_password_confirmation: confirmation,
      }),
    });
    message(data.detail || "Mot de passe réinitialisé", "success");
    $("forgot-form").reset();
    afficherFormulaire("login-form");
  } catch (err) {
    message(err.message, "error");
  }
});

document.querySelectorAll(".auth-tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    afficherFormulaire(tab.dataset.form);
    message("");
  })
);

$("show-forgot").addEventListener("click", (e) => {
  e.preventDefault();
  document.querySelectorAll(".auth-tab").forEach((t) => t.classList.remove("active"));
  document.querySelectorAll(".auth-form").forEach((f) => (f.style.display = "none"));
  $("forgot-form").style.display = "block";
  message("");
});

$("back-login").addEventListener("click", (e) => {
  e.preventDefault();
  afficherFormulaire("login-form");
  message("");
});

$("logout-btn").addEventListener("click", logout);

/* ===== Accueil : statistiques ===== */
async function chargerStats() {
  try {
    const factures = await api("/api/factures");
    const fournisseurs = new Set(factures.map((f) => (f.fournisseur || "INCONNU").toLowerCase())).size;
    const aRisque = factures.filter(
      (f) => f.fraude && (f.fraude.risk_score >= 50 || f.fraude.classification === "FRAUDE_POTENTIELLE")
    ).length;

    const valeurs = [
      String(factures.length),
      String(fournisseurs),
      String(aRisque),
      "100%",
    ];
    document.querySelectorAll("#home-stats .stat-value").forEach((el, i) => {
      el.textContent = valeurs[i] || "—";
    });
  } catch (e) {}
}

/* ===== Tableau de bord : répartition par fournisseur + historique ===== */
async function chargerDashboard() {
  const rep = document.querySelector("#dash-partage tbody");
  const tot = document.getElementById("dash-totaux");
  const hist = document.querySelector("#historique-liste tbody");
  if (!rep) return;

  try {
    const [stats, histo] = await Promise.all([
      api("/api/stats").catch(() => null),
      api("/api/historique").catch(() => []),
    ]);

    /* --- Répartition par fournisseur (TND) --- */
    if (stats && Array.isArray(stats.par_fournisseur)) {
      const max = Math.max(...stats.par_fournisseur.map((p) => p.total_ttc || 0), 1);
      rep.innerHTML = stats.par_fournisseur.length
        ? stats.par_fournisseur
            .map(
              (p) => `
                <tr>
                  <td><b>${escapeHtml(p.fournisseur || "INCONNU")}</b></td>
                  <td class="num">${p.nombre}</td>
                  <td class="num">${montant(p.total_ttc)}</td>
                  <td>
                    <div class="bar" style="background:#ede9fb"><span style="width:${Math.round(((p.total_ttc || 0) / max) * 100)}%"></span></div>
                    ${Math.round((p.total_ttc || 0) / (stats.total_ttc || 1) * 100)}%
                  </td>
                </tr>`,
            )
            .join("")
        : `<tr><td colspan="4" class="muted" style="text-align:center;padding:18px">Aucune facture pour l'instant.</td></tr>`;

      if (tot)
        tot.innerHTML = `<b>${stats.total_factures}</b> facture(s) · Total TTC : <b>${montant(stats.total_ttc)}</b> · HT : ${montant(stats.total_ht)} · TVA : ${montant(stats.total_tva)} · ${stats.nombre_a_risque} à risque · ${stats.recentes_7j} ce mois-ci (TND).`;
    } else {
      rep.innerHTML = `<tr><td colspan="4" class="muted" style="text-align:center;padding:18px">Les statistiques ne sont pas disponibles.</td></tr>`;
    }

    /* --- Historique / traçabilité --- */
    if (hist)
      hist.innerHTML = Array.isArray(histo) && histo.length
        ? histo
            .map(
              (e) => `
                <tr>
                  <td>${escapeHtml(e.created_at || "—")}</td>
                  <td><span class="badge" style="text-transform:capitalize">${escapeHtml(e.action || "—")}</span></td>
                  <td>${escapeHtml(e.description || "—")}</td>
                  <td>TND</td>
                  <td>${e.facture_id ? '<a class="link" onclick="ouvrirFacture(' + e.facture_id + ');return false;">#' + e.facture_id + "</a>" : "—"}</td>
                </tr>`,
            )
            .join("")
        : `<tr><td colspan="5" class="muted" style="text-align:center;padding:18px">Aucune action de travail enregistrée.</td></tr>`;
  } catch (e) {}
}

/* ===== Pipeline : Télécharger → OCR → Fraude → Analyser ===== */
const etat = {
  uploadId: null,
  filename: "",
  fichier: null,
  fichiers: [],
  texteOcr: null,
  donnees: null,
  analyseFraude: null,
  resultat: null,
};

const dropZone = $("drop-zone");
const fileInput = $("file-input");

dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.add("dragover");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragover");
  if (e.dataTransfer.files.length) {
    fileInput.files = e.dataTransfer.files;
    selectionnerFichiers(Array.from(e.dataTransfer.files));
  }
});

$("select-file-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  fileInput.click();
});

fileInput.addEventListener("change", () => {
  if (fileInput.files.length) selectionnerFichiers(Array.from(fileInput.files));
});

function selectionnerFichiers(fichiers) {
  if (!fichiers.length) return;
  if (fichiers.length === 1) return selectionnerFichier(fichiers[0]);
  etat.fichiers = fichiers;
  etat.filename = `${fichiers.length} fichier(s)`;
  etat.resultat = null;
  $("file-chip-wrap").innerHTML = fichiers
    .map((f) => `<span class="file-chip">📄 ${escapeHtml(f.name)}</span>`)
    .join("");
  reinitialiserPipeline();
  afficherChargement(true, `Analyse en lot : ${fichiers.length} factures…`);
  executerBatch(fichiers);
}

function selectionnerFichier(fichier) {
  etat.fichier = fichier;
  etat.filename = fichier.name;
  $("file-chip-wrap").innerHTML = `<span class="file-chip">📄 ${escapeHtml(fichier.name)}</span>`;
  reinitialiserPipeline();
  activerEtape("upload");
  executerPipeline();
}

async function executerBatch(fichiers) {
  try {
    const form = new FormData();
    fichiers.forEach((f) => form.append("files", f));
    const data = await api("/api/factures/batch", { method: "POST", body: form });
    afficherResultatBatch(data);
  } catch (err) {
    toast(err.message, "error");
    afficherChargement(false);
  }
}

async function executerPipeline() {
  verrouillerPipeline();
  afficherChargement(true, "Traitement automatique en cours…");
  try {
    for (const step of ["upload", "ocr", "fraude", "analyser"]) {
      await executerEtape(step);
      if (etat.resultat) break;
    }
  } finally {
    afficherChargement(false);
  }
}

function afficherChargement(oui, texte) {
  const wrap = $("upload-loading");
  if (oui) {
    $("upload-loading-text").textContent = texte || "Traitement automatique en cours…";
    wrap.style.display = "flex";
  } else {
    wrap.style.display = "none";
  }
}

function reinitialiserPipeline() {
  etat.uploadId = null;
  etat.texteOcr = null;
  etat.donnees = null;
  etat.analyseFraude = null;
  etat.resultat = null;
  $("upload-result").style.display = "none";
  $("upload-result").innerHTML = "";
  ["step-upload", "step-ocr", "step-fraude", "step-analyser"].forEach((id) => {
    const btn = $(id);
    btn.classList.remove("done", "error");
    btn.querySelector("[data-status]").textContent = "→";
    btn.disabled = true;
  });
}

function activerEtape(step) {
  $("step-" + step).disabled = false;
}

function verrouillerPipeline() {
  ["step-upload", "step-ocr", "step-fraude", "step-analyser"].forEach((id) => {
    $(id).disabled = true;
  });
}

function marquerEtape(step, ok) {
  const btn = $("step-" + step);
  btn.classList.remove("error");
  if (ok) {
    btn.classList.add("done");
    btn.querySelector("[data-status]").textContent = "✓";
  } else {
    btn.classList.add("error");
    btn.querySelector("[data-status]").textContent = "✕";
  }
}

async function executerEtape(step) {
  const btn = $("step-" + step);
  btn.disabled = true;
  btn.classList.add("loading");
  btn.querySelector("[data-status]").textContent = "…";
  try {
    let data;
    if (step === "upload") {
      const form = new FormData();
      form.append("file", fileInput.files[0]);
      data = await api("/api/factures/upload", { method: "POST", body: form });
      etat.uploadId = data.upload_id;
      marquerEtape("upload", true);
      activerEtape("ocr");
    } else if (step === "ocr") {
      data = await api(`/api/factures/${etat.uploadId}/ocr`, { method: "POST" });
      etat.texteOcr = data.texte_ocr;
      etat.donnees = data.donnees;
      if (data.avertissement) toast(data.avertissement, "warning");
      marquerEtape("ocr", true);
      activerEtape("fraude");
    } else if (step === "fraude") {
      data = await api(`/api/factures/${etat.uploadId}/fraude`, { method: "POST" });
      etat.analyseFraude = data.analyse_fraude;
      marquerEtape("fraude", true);
      activerEtape("analyser");
    } else if (step === "analyser") {
      data = await api(`/api/factures/${etat.uploadId}/analyser`, { method: "POST" });
      etat.resultat = data;
      marquerEtape("analyser", true);
      verrouillerPipeline();
      afficherResultat(data);
      toast(`Facture analysée — identifiant ${data.identifiant}`, "success");
    }
  } catch (err) {
    marquerEtape(step, false);
    activerEtape(step);
    if (String(err.message).includes("Fichier introuvable ou expiré")) {
      toast("Le fichier a expiré (serveur redémarré ou délai dépassé). Cliquez sur « ＋ Nouvelle facture » et recommencez.", "error");
    } else {
      toast("Erreur : " + err.message, "error");
    }
  } finally {
    btn.classList.remove("loading");
  }
}

document.querySelectorAll(".pipeline-btn").forEach((b) =>
  b.addEventListener("click", () => executerEtape(b.dataset.step))
);

/* ===== Résultats par sections ===== */
function afficherResultat(data) {
  const id = data.id;
  const imgUrl = `/file/image/${id}`;
  const pdfUrl = `/file/pdf/${id}`;
  const ident = data.identifiant || "";
  const identParts = ident.split("-");

  $("upload-result").innerHTML = `
    <div class="ident-banner">
      <div>
        <div class="ident-label">Identifiant unique de la facture</div>
        <div class="ident-value">${escapeHtml(identParts[0] || ident)}-<b>${escapeHtml(identParts.slice(1).join("-"))}</b></div>
      </div>
      <div class="ident-actions">
        <a class="btn btn-accent btn-sm" href="${pdfUrl}" target="_blank">⬇ Télécharger le PDF</a>
        <button class="btn btn-outline btn-sm" onclick="voirFacture(${id}); return false;">👁 Voir le détail</button>
        <button class="btn btn-primary btn-sm" onclick="nouvellAnalyse(); return false;">＋ Nouvelle facture</button>
      </div>
    </div>

    <div class="result-section">
      <div class="section-head"><span class="sec-ico">📄</span><h3>Document source</h3></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px" class="doc-grid">
        <div><img class="result-img" src="${imgUrl}" alt="Facture"></div>
        <div>
          <div class="info-grid" style="grid-template-columns:1fr">
            ${paireInfo("Fournisseur", data.donnees && data.donnees.fournisseur)}
            ${paireInfo("N° de facture", data.donnees && data.donnees.numero_facture)}
            ${paireInfo("Date", data.donnees && data.donnees.date_facture)}
          </div>
          <p class="muted" style="margin-top:14px">
            <a class="link" href="${pdfUrl}" target="_blank">Télécharger le PDF de la facture</a>
          </p>
        </div>
      </div>
    </div>

    ${htmlSectionFacture(data.donnees || {})}
    ${htmlSectionPatient(data.donnees || {})}
    ${htmlSectionProfessionnel(data.donnees || {})}
    ${htmlSectionActes(data.donnees || {})}
    ${htmlSectionRemboursement(data.donnees || {})}
    ${htmlSectionFraude(data.analyse_fraude || data.fraude, data.analyse_visuelle)}
    ${htmlSectionOcr(data.texte_ocr)}`;

  $("upload-result").style.display = "block";
  $("upload-result").scrollIntoView({ behavior: "smooth", block: "start" });
}

function afficherResultatBatch(data) {
  afficherChargement(false);
  const groupes = data.groupes || [];
  const erreurs = data.erreurs || [];

  let html = `
    <div class="ident-banner">
      <div>
        <div class="ident-label">Analyse en lot</div>
        <div class="ident-value"><b>${data.traitees}</b> facture(s) analysée(s)${erreurs.length ? `, <b>${erreurs.length}</b> en échec` : ""}</div>
      </div>
      <div class="ident-actions">
        <button class="btn btn-primary btn-sm" onclick="nouvellAnalyse(); return false;">＋ Nouvelle analyse</button>
      </div>
    </div>`;

  if (erreurs.length) {
    html += `
      <div class="result-section">
        <div class="section-head"><span class="sec-ico">⚠️</span><h3>Fichiers en échec</h3></div>
        <ul class="fraud-list">${erreurs.map((e) => `<li><b>${escapeHtml(e.fichier)}</b> — ${escapeHtml(e.erreur)}</li>`).join("")}</ul>
      </div>`;
  }

  html += `<p class="muted" style="margin:14px 0">Les résultats sont regroupés par fournisseur (comme la section « Fournisseurs »).</p>`;
  html += renderGroupes(groupes);

  $("upload-result").innerHTML = html;
  $("upload-result").style.display = "block";
  $("upload-result").scrollIntoView({ behavior: "smooth", block: "start" });
  chargerStats();
}

function paireInfo(k, v) {
  return `<div class="info-item"><div class="k">${escapeHtml(k)}</div><div class="v">${valeur(v)}</div></div>`;
}

function htmlSectionFacture(d) {
  const grid = [
    paireInfo("Fournisseur", d.fournisseur),
    paireInfo("N° de facture", d.numero_facture),
    paireInfo("Date de facture", d.date_facture),
    paireInfo("Devise", "TND"),
    paireInfo("Montant HT", montant(d.montant_ht)),
    paireInfo("TVA", montant(d.tva)),
    paireInfo("Remise", montant(d.remise)),
    paireInfo("Sous-total", montant(d.sous_total)),
    paireInfo("Montant TTC", montant(d.montant_ttc)),
    paireInfo("Statut de paiement", d.statut_paiement),
    paireInfo("Mode de paiement", d.mode_paiement),
  ].join("");
  return `<div class="result-section">
    <div class="section-head"><span class="sec-ico">🧾</span><h3>Informations de la facture</h3></div>
    <div class="info-grid">${grid}</div>
  </div>`;
}

function htmlSectionPatient(d) {
  const p = d.patient || {};
  const grid = [
    paireInfo("Nom", p.nom),
    paireInfo("Prénom", p.prenom),
    paireInfo("Date de naissance", p.date_naissance),
    paireInfo("N° d'assuré", p.numero_assure),
    paireInfo("Adresse", p.adresse),
    paireInfo("Organisme d'assurance", p.organisme_assurance),
  ].join("");
  return `<div class="result-section">
    <div class="section-head"><span class="sec-ico">👤</span><h3>Patient / Assuré</h3></div>
    <div class="info-grid">${grid}</div>
  </div>`;
}

function htmlSectionProfessionnel(d) {
  const p = d.professionnel || {};
  const grid = [
    paireInfo("Nom", p.nom),
    paireInfo("Spécialité", p.specialite),
    paireInfo("Identifiant professionnel", p.identifiant_professionnel),
    paireInfo("Matricule fiscal", p.matricule_fiscal),
    paireInfo("Adresse", p.adresse),
    paireInfo("Téléphone", p.telephone),
  ].join("");
  return `<div class="result-section">
    <div class="section-head"><span class="sec-ico">👨‍⚕️</span><h3>Professionnel de santé</h3></div>
    <div class="info-grid">${grid}</div>
  </div>`;
}

function htmlSectionActes(d) {
  const actes = Array.isArray(d.actes) ? d.actes : [];
  let tableau = `<p class="muted">Aucun acte identifiable.</p>`;
  if (actes.length) {
    const lignes = actes.map((a) => `
      <tr>
        <td>${escapeHtml(a.date || "—")}</td>
        <td>${escapeHtml(a.description || "—")}</td>
        <td class="num">${a.quantite != null ? a.quantite : "—"}</td>
        <td class="num">${montant(a.prix_unitaire) || "—"}</td>
        <td class="num"><b>${montant(a.montant) || "—"}</b></td>
      </tr>`).join("");
    tableau = `
      <div style="overflow-x:auto">
        <table class="data-table">
          <thead><tr><th>Date</th><th>Description</th><th>Qté</th><th>PU</th><th>Montant</th></tr></thead>
          <tbody>${lignes}</tbody>
        </table>
      </div>`;
  }
  return `<div class="result-section">
    <div class="section-head"><span class="sec-ico">💊</span><h3>Actes médicaux</h3></div>
    ${tableau}
  </div>`;
}

function htmlSectionRemboursement(d) {
  const r = d.remboursement || {};
  const grid = [
    paireInfo("Montant total", montant(r.montant_total)),
    paireInfo("Montant pris en charge", montant(r.montant_pris_en_charge)),
    paireInfo("Part patient", montant(r.part_patient)),
    paireInfo("Reste à payer", montant(r.reste_a_payer)),
    paireInfo("Taux de remboursement", r.taux_remboursement != null ? r.taux_remboursement + " %" : null),
  ].join("");
  return `<div class="result-section">
    <div class="section-head"><span class="sec-ico">💰</span><h3>Remboursement</h3></div>
    <div class="info-grid">${grid}</div>
  </div>`;
}

/* ===== Analyse anti-fraude ===== */
function riskClasse(score) {
  if (score == null) return "neutral";
  if (score <= 20) return "very-low";
  if (score <= 40) return "low";
  if (score <= 60) return "medium";
  if (score <= 80) return "high";
  return "very-high";
}

function badgeRisque(fraude) {
  if (!fraude || !fraude.classification) {
    return `<span class="risk-badge risk-none">—</span>`;
  }
  const cls = fraude.classification.toLowerCase().replace(/_/g, "-");
  const score = fraude.risk_score != null ? ` · ${fraude.risk_score}/100` : "";
  return `<span class="risk-badge risk-${cls}" title="Confiance : ${fraude.confidence != null ? fraude.confidence + "/100" : "n/a"}">${escapeHtml(fraude.classification.replace(/_/g, " "))}${score}</span>`;
}

function listeHtml(arr) {
  if (arr == null) return `<li class="muted">Aucun élément.</li>`;
  if (typeof arr === "string") {
    return arr.trim() ? `<li>${escapeHtml(arr)}</li>` : `<li class="muted">Aucun élément.</li>`;
  }
  if (!Array.isArray(arr) || !arr.length) return `<li class="muted">Aucun élément.</li>`;
  return arr.map((s) => `<li>${escapeHtml(String(s))}</li>`).join("");
}

function htmlSectionFraude(fraude, visuel) {
  if (!fraude || !fraude.classification) {
    return `<div class="result-section">
      <div class="section-head"><span class="sec-ico">🛡️</span><h3>Analyse anti-fraude</h3></div>
      <p class="muted">Analyse non disponible pour cette facture.</p>
    </div>`;
  }

  const cls = fraude.classification.toLowerCase().replace(/_/g, "-");
  const sc = riskClasse(fraude.risk_score);

  const pills = [];
  const indiceVisuel = (detectee, libelle) => {
    if (detectee === undefined || detectee === null) return "";
    return `<span class="check-pill ${detectee ? "check-ok" : "check-bad"}">${libelle}</span>`;
  };

  if (visuel) {
    if (visuel.signature && visuel.signature.detected !== undefined) {
      const det = visuel.signature.detected;
      pills.push(`<span class="check-pill ${det ? "check-ok" : "check-bad"}">Signature ${det ? "existe" : "manquante"}</span>`);
    }
    if (visuel.tampon && visuel.tampon.detected !== undefined) {
      const det = visuel.tampon.detected;
      pills.push(`<span class="check-pill ${det ? "check-ok" : "check-bad"}">Tampon ${det ? "existe" : "manquant"}</span>`);
    }
  } else {
    pills.push(indiceVisuel(fraude.signature_detectee, "Signature " + (fraude.signature_detectee ? "existe" : "manquante")));
    pills.push(indiceVisuel(fraude.tampon_detecte, "Tampon " + (fraude.tampon_detecte ? "existe" : "manquant")));
  }

  return `
  <div class="result-section">
    <div class="section-head"><span class="sec-ico">🛡️</span><h3>Analyse anti-fraude</h3></div>

    <div class="fraud-summary">
      <div class="fraud-metric"><div class="m-label">Classification</div>
        <div style="margin-top:8px"><span class="risk-badge risk-${cls}">${escapeHtml(fraude.classification.replace(/_/g, " "))}</span></div></div>
      <div class="fraud-metric"><div class="m-label">Score de risque</div>
        <div class="m-value">${fraude.risk_score != null ? fraude.risk_score + " / 100" : "—"}</div></div>
      <div class="fraud-metric"><div class="m-label">Confiance</div>
        <div class="m-value">${fraude.confidence != null ? fraude.confidence + " %" : "—"}</div></div>
    </div>

    ${pills.filter(Boolean).length ? `<div class="check-pills">${pills.filter(Boolean).join("")}</div>` : ""}

    <p class="ktitle">Indicateurs de fraude</p>
    <ul class="fraud-list">${listeHtml(fraude.fraud_indicators)}</ul>

    <p class="ktitle">Informations manquantes</p>
    <ul class="fraud-list">${listeHtml(fraude.missing_information)}</ul>

    <p class="ktitle">Recommandations</p>
    <ul class="fraud-list">${listeHtml(fraude.recommendations)}</ul>
  </div>`;
}

function htmlSectionOcr(texte) {
  return `<div class="result-section">
    <div class="section-head"><span class="sec-ico">🔤</span><h3>Texte OCR brut</h3></div>
    <div class="ocr-box">${escapeHtml(texte || "Aucun texte extrait.")}</div>
  </div>`;
}

function nouvellAnalyse() {
  $("file-input").value = "";
  etat.fichiers = [];
  etat.fichier = null;
  reinitialiserPipeline();
  $("file-chip-wrap").innerHTML = "";
  $("upload-result").scrollIntoView({ behavior: "smooth", block: "start" });
}

/* À chaque erreur JS : on l'affiche en rouge au lieu de rester muet (diagnostic). */
window.addEventListener("error", (e) => {
  try {
    toast(
      "Erreur JS: " + (e.message || "inconnue") +
      (e.lineno ? " (ligne " + e.lineno + ")" : ""),
      "error"
    );
  } catch (_) {}
});

/* ===== Liste / Recherche ===== */
$("search-btn").addEventListener("click", () => rechercher());
$("search-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") rechercher();
});

async function rechercher() {
  const q = $("search-input").value.trim();
  if (!q) return loadInvoices();
  try {
    const data = await api(`/api/factures/recherche?fournisseur=${encodeURIComponent(q)}`);
    afficherTable(data);
  } catch (err) {
    toast(err.message, "error");
  }
}

async function loadInvoices() {
  try {
    const data = await api("/api/factures");
    afficherTable(data);
  } catch (err) {
    toast(err.message, "error");
  }
}

function afficherTable(factures) {
  const wrap = $("invoices-table-wrap");
  if (!factures.length) {
    wrap.innerHTML = `<div class="empty-state"><div class="em-ico">🗂️</div>Aucune facture pour le moment.<br><br><a class="btn btn-primary" href="#" onclick="goPage('upload-tab'); return false;">Analyser ma première facture</a></div>`;
    return;
  }

  const lignes = factures.map((f) => `
    <tr>
      <td><input type="checkbox" class="sel-check" value="${f.id}" ${selectionFactures.has(f.id) ? "checked" : ""} onchange="toggleSelection(${f.id}, this.checked)"></td>
      <td><span class="ident-chip">${escapeHtml(f.identifiant || "—")}</span></td>
      <td><span class="badge">${escapeHtml(f.fournisseur || "INCONNU")}</span></td>
      <td>${escapeHtml(f.numero_facture || "—")}</td>
      <td>${escapeHtml(f.date_facture || "—")}</td>
          <td>${f.montant_ttc ? montant(f.montant_ttc) : "—"}</td>
      <td>${badgeRisque(f.fraude)}</td>
      <td class="actions">
        <a class="link" href="#" onclick="voirFacture(${f.id}); return false;">Voir</a>
        <a class="link" href="/file/pdf/${f.id}" target="_blank">PDF</a>
        <a class="link danger" href="#" onclick="supprimerFacture(${f.id}); return false;">Supprimer</a>
      </td>
    </tr>`).join("");

  wrap.innerHTML = `
    <div style="overflow-x:auto">
      <table class="invoices">
        <thead>
          <tr>
            <th><input type="checkbox" class="sel-check" id="sel-tout" onchange="toggleToutSelection([${factures.map((f) => f.id).join(",")}], this.checked)" ${factures.length && factures.length === selectionFactures.size ? "checked" : ""}></th>
            <th>Identifiant</th>
            <th>Fournisseur</th>
            <th>N° facture</th>
            <th>Date</th>
            <th>TTC</th>
            <th>Risque</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>${lignes}</tbody>
      </table>
    </div>
    <p class="muted" style="margin-top:12px">${factures.length} facture(s) — <b>${selectionFactures.size}</b> sélectionnée(s)</p>`;
}

/* ===== Fournisseurs ===== */
function renderGroupes(groupes) {
  let total = 0;
  const sections = groupes.map((g) => {
    total += g.nombre;
    const lignes = g.factures.map((f) => `
        <tr>
          <td><span class="ident-chip">${escapeHtml(f.identifiant || "—")}</span></td>
          <td>${escapeHtml(f.numero_facture || "—")}</td>
          <td>${escapeHtml(f.date_facture || "—")}</td>
      <td>${montant(f.montant_ttc) || "—"}</td>
          <td>${badgeRisque(f.fraude)}</td>
          <td class="actions">
            <a class="link" href="#" onclick="voirFacture(${f.id}); return false;">Voir</a>
            <a class="link" href="/file/pdf/${f.id}" target="_blank">PDF</a>
          </td>
        </tr>`).join("");

    return `
        <div class="supplier-block">
          <div class="supplier-header">
            <span class="badge">${escapeHtml(g.fournisseur)}</span>
            <span class="count-badge">${g.nombre} facture${g.nombre > 1 ? "s" : ""}</span>
          </div>
          <div style="overflow-x:auto">
            <table class="invoices">
              <thead>
                <tr>
                  <th>Identifiant</th>
                  <th>N° facture</th>
                  <th>Date</th>
                  <th>TTC</th>
                  <th>Risque</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>${lignes}</tbody>
            </table>
          </div>
        </div>`;
  }).join("");

  return `
    <p class="muted" style="margin-bottom:14px">Total : <b>${total} facture(s)</b> pour <b>${groupes.length} fournisseur(s)</b>.</p>
    ${sections}`;
}

async function chargerFournisseurs() {
  try {
    if (selectionFactures.size) return afficherSelection();
    const groupes = await api("/api/factures/par-fournisseur");
    const wrap = $("suppliers-wrap");
    if (!groupes.length) {
      wrap.innerHTML = `<div class="empty-state"><div class="em-ico">🏥</div>Aucune facture pour le moment.</div>`;
      return;
    }
    wrap.innerHTML = renderGroupes(groupes);
  } catch (err) {
    toast(err.message, "error");
  }
}

/* ===== Modal : détail d'une facture ===== */
async function voirFacture(id) {
  try {
    const f = await api(`/api/factures/${id}`);
    const imgUrl = `/file/image/${id}`;
    const pdfUrl = `/file/pdf/${id}`;
    const donnees = f.donnees || {};
    const ident = f.identifiant || "";
    const identParts = ident.split("-");

    $("modal-body").innerHTML = `
      <div class="ident-banner" style="margin-top:8px">
        <div>
          <div class="ident-label">Identifiant unique</div>
          <div class="ident-value">${escapeHtml(identParts[0] || ident)}-<b>${escapeHtml(identParts.slice(1).join("-"))}</b></div>
        </div>
        <div class="ident-actions">
          <a class="btn btn-accent btn-sm" href="${pdfUrl}" target="_blank">⬇ PDF</a>
          <button class="btn btn-outline btn-sm" onclick="reanalyserFacture(${id}); return false;">↻ Réanalyser la fraude</button>
        </div>
      </div>

      <div class="result-section">
        <div class="section-head"><span class="sec-ico">📄</span><h3>Document source</h3></div>
        <img src="${imgUrl}" alt="Facture">
        <p class="muted" style="margin-top:10px"><a class="link" href="${pdfUrl}" target="_blank">Télécharger le PDF</a></p>
      </div>

      ${htmlSectionFacture(donnees)}
      ${htmlSectionPatient(donnees)}
      ${htmlSectionProfessionnel(donnees)}
      ${htmlSectionActes(donnees)}
      ${htmlSectionRemboursement(donnees)}
      ${htmlSectionFraude(f.fraude)}
    `;
    $("modal").style.display = "flex";
  } catch (err) {
    toast(err.message, "error");
  }
}

async function reanalyserFacture(id) {
  if (!confirm("Relancer l'analyse anti-fraude ? (OCR + analyse IA, environ 1 minute)")) return;
  try {
    await api(`/api/factures/${id}/reanalyser-fraude`, { method: "POST" });
    toast("Analyse anti-fraude mise à jour", "success");
    voirFacture(id);
  } catch (err) {
    toast(err.message, "error");
  }
}

async function supprimerFacture(id) {
  if (!confirm("Supprimer cette facture ? (image + PDF + enregistrement)")) return;
  try {
    await api(`/api/factures/${id}`, { method: "DELETE" });
    selectionFactures.delete(id);
    majBoutonSelection();
    toast("Facture supprimée", "success");
    loadInvoices();
    chargerStats();
  } catch (err) {
    toast(err.message, "error");
  }
}

$("modal-close").addEventListener("click", () => ($("modal").style.display = "none"));
$("modal").addEventListener("click", (e) => {
  if (e.target === $("modal")) $("modal").style.display = "none";
});

/* ===== À propos (clic sur le logo) ===== */
function afficherAPropos() {
  $("modal-body").innerHTML = `
    <h3 style="margin-bottom:4px">OCR Facture</h3>
    <p class="muted" style="margin-bottom:14px">Système intelligent d'extraction et d'analyse de factures de soins</p>

    <div class="about-section" style="margin-bottom:14px">
      <p style="line-height:1.6">
        <b>OCR</b> signifie <b>Optical Character Recognition</b> (reconnaissance optique de caractères).
        Cette technologie permet à la machine de « lire » une image ou un scan et d'en extraire le texte.
        OCR Facture l'applique aux factures de soins pour transformer un document papier en données exploitables.
      </p>
    </div>

    <div class="about-section" style="margin-bottom:14px">
      <h4 style="color:var(--bleu-600)">Fonctionnalités</h4>
      <ul class="fraud-list">
        <li><b>Extraction OCR</b> : lecture automatique de la facture (manuscrite ou imprimée).</li>
        <li><b>Extraction intelligente</b> : patient, professionnel, actes, remboursement, montants.</li>
        <li><b>Détection de fraude</b> : classification, score de risque, indicateurs et recommandations.</li>
        <li><b>Identifiant unique</b> : chaque facture reçoit un code FAC-XXXX-XXXX.</li>
        <li><b>Organisation automatique</b> : PDF généré et classement par professionnel.</li>
        <li><b>Assistant IA (InvoiceBot)</b> : répond aux questions sur vos factures.</li>
      </ul>
    </div>

    <div class="about-section">
      <h4 style="color:var(--bleu-600)">Sécurité</h4>
      <p style="line-height:1.6">
        Authentification sécurisée (bcrypt, sessions HttpOnly) : chaque utilisateur n'accède qu'à ses propres factures.
      </p>
    </div>`;
  $("modal").style.display = "flex";
}

$("logo-about-auth").addEventListener("click", afficherAPropos);
$("logo-about-nav").addEventListener("click", () => goPage("home-tab"));

/* ===== Mon compte ===== */
async function chargerCompte() {
  try {
    const data = await api("/api/auth/me");
    const role = data.role === "ADMIN" ? "Administrateur" : "Utilisateur";
    $("account-infos").innerHTML =
      "Connecté en tant que <b>" + escapeHtml(data.username) + "</b> — rôle : " + role +
      (data.full_name ? " — " + escapeHtml(data.full_name) : "") +
      (data.last_login ? " — dernière connexion : " + new Date(data.last_login).toLocaleString("fr-FR") : "");
  } catch (e) {
    $("account-infos").textContent = "Impossible de récupérer les informations du compte.";
  }
  $("chg-message").textContent = "";
  $("chg-message").className = "auth-message";
}

$("chg-password-btn").addEventListener("click", async () => {
  const bouton = $("chg-password-btn");
  const messageEl = $("chg-message");
  await avecChargement(bouton, "Enregistrement…", async () => {
    messageEl.textContent = "";
    messageEl.className = "auth-message";
    try {
      const nouveau = $("chg-new-password").value;
      const confirmation = $("chg-new-password-confirm").value;
      if (nouveau !== confirmation) {
        messageEl.textContent = "Les mots de passe ne correspondent pas";
        messageEl.className = "auth-message error";
        return;
      }
      const data = await api("/api/auth/change-password", {
        method: "POST",
        body: JSON.stringify({
          current_password: $("chg-current-password").value,
          new_password: nouveau,
          new_password_confirmation: confirmation,
        }),
      });
      messageEl.textContent = data.detail || "Mot de passe modifié avec succès.";
      messageEl.className = "auth-message success";
      $("chg-current-password").value = "";
      $("chg-new-password").value = "";
      $("chg-new-password-confirm").value = "";
    } catch (err) {
      messageEl.textContent = err.message;
      messageEl.className = "auth-message error";
    }
  });
});

/* ===== Assistant IA (InvoiceBot) ===== */
const chatWindow = $("chat-window");
const chatInput = $("chat-input");
const chatHistorique = [];
const PROMPT_KEY = "chatbot_prompt";

function chargerPrompt() {
  const p = localStorage.getItem(PROMPT_KEY) || "";
  $("chat-prompt").value = p;
}

chargerPrompt();

$("chat-prompt-reset").addEventListener("click", () => {
  localStorage.removeItem(PROMPT_KEY);
  $("chat-prompt").value = "";
  toast("Prompt réinitialisé");
});

function ajouterMessage(role, contenu) {
  const div = document.createElement("div");
  div.className = `chat-msg ${role === "user" ? "user" : "bot"}`;
  div.textContent = contenu;
  chatWindow.appendChild(div);
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

async function envoyerQuestion() {
  const question = chatInput.value.trim();
  if (!question) return;

  ajouterMessage("user", question);
  chatInput.value = "";

  const promptPerso = ($("chat-prompt").value || "").trim();
  localStorage.setItem(PROMPT_KEY, promptPerso);

  const attente = document.createElement("div");
  attente.className = "chat-msg bot typing";
  attente.textContent = "Réflexion en cours…";
  chatWindow.appendChild(attente);
  chatWindow.scrollTop = chatWindow.scrollHeight;

  const bouton = $("chat-send-btn");
  bouton.disabled = true;
  try {
    const data = await api("/api/chatbot/message", {
      method: "POST",
      body: JSON.stringify({
        message: question,
        prompt_personnalise: promptPerso,
        historique: chatHistorique,
      }),
    });
    attente.remove();
    ajouterMessage("bot", data.reponse);
    chatHistorique.push({ role: "user", content: question });
    chatHistorique.push({ role: "model", content: data.reponse });
    if (chatHistorique.length > 20) chatHistorique.splice(0, chatHistorique.length - 20);
  } catch (err) {
    attente.remove();
    ajouterMessage("bot", "Erreur : " + err.message);
  } finally {
    bouton.disabled = false;
    chatInput.focus();
  }
}

$("chat-send-btn").addEventListener("click", envoyerQuestion);
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") envoyerQuestion();
});

$("chat-print-btn").addEventListener("click", () => {
  if (!chatHistorique.length && chatWindow.children.length <= 1) {
    toast("Aucune réponse à imprimer pour le moment");
    return;
  }
  window.print();
});

$("chat-settings-toggle").addEventListener("click", () => {
  const p = $("chat-prompt-panel");
  p.style.display = p.style.display === "none" ? "block" : "none";
});

/* ===== Initialisation : session via cookie HttpOnly ===== */
(async function init() {
  try {
    const data = await api("/api/auth/me");
    username = data.username;
    localStorage.setItem("username", username);
    showApp();
  } catch (e) {
    showAuth();
  }
  majBoutonSelection();
})();
