from fastapi import APIRouter, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from jinja2 import Template

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard - {{ azienda.nome }}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
    <div class="container py-5">
        <h1 class="mb-4">Pannello di Controllo - {{ azienda.nome }}</h1>
        
        <div class="row">
            <!-- ISTRUZIONI IA -->
            <div class="col-md-5 mb-4">
                <div class="card shadow-sm">
                    <div class="card-header bg-primary text-white">
                        <h5 class="card-title mb-0">Istruzioni IA WhatsApp</h5>
                    </div>
                    <div class="card-body">
                        <form action="/dashboard/{{ azienda.id }}/update-prompt" method="post">
                            <div class="mb-3">
                                <label class="form-label">Nome Attività</label>
                                <input type="text" name="nome" class="form-control" value="{{ azienda.nome }}" required>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Regole, Orari e Listino Servizi</label>
                                <textarea name="istruzioni_ia" class="form-control" rows="10" required>{{ azienda.istruzioni_ia }}</textarea>
                            </div>
                            <button type="submit" class="btn btn-success w-100">Salva Modifiche</button>
                        </form>
                    </div>
                </div>
            </div>

            <div class="col-md-7">
                <!-- APPUNTAMENTI PRENOTATI -->
                <div class="card shadow-sm mb-4">
                    <div class="card-header bg-dark text-white d-flex justify-content-between align-items-center">
                        <h5 class="card-title mb-0">Appuntamenti Prenotati</h5>
                        <span class="badge bg-success">{{ appuntamenti|length }} Prenotazioni</span>
                    </div>
                    <div class="card-body">
                        {% if appuntamenti %}
                        <div class="table-responsive">
                            <table class="table table-hover">
                                <thead>
                                    <tr>
                                        <th>Data e Ora</th>
                                        <th>Cliente</th>
                                        <th>Servizio</th>
                                        <th>Azione</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for slot in appuntamenti %}
                                    <tr>
                                        <td><strong>{{ slot.data_ora.split(' ')[0].split('-')[2] }}/{{ slot.data_ora.split(' ')[0].split('-')[1] }}/{{ slot.data_ora.split(' ')[0].split('-')[0] }} {{ slot.data_ora.split(' ')[1] }}</strong></td>
                                        <td>{{ slot.cliente_nome or 'N/D' }}</td>
                                        <td><span class="badge bg-info text-dark">{{ slot.servizio or 'Generale' }}</span></td>
                                        <td>
                                            <form action="/dashboard/{{ azienda.id }}/delete-slot/{{ slot.id }}" method="post" style="display:inline;">
                                                <button type="submit" class="btn btn-sm btn-outline-danger">Cancella</button>
                                            </form>
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                        {% else %}
                        <p class="text-muted text-center py-4">Nessun appuntamento registrato al momento.</p>
                        {% endif %}
                    </div>
                </div>

                <!-- EMAIL MARKETING B2B AUTOMATICO SU DOMINIO -->
                <div class="card shadow-sm">
                    <div class="card-header bg-warning text-dark">
                        <h5 class="card-title mb-0">🤖 Bot Email Marketing su Dominio</h5>
                    </div>
                    <div class="card-body">
                        <div class="row g-2 mb-3">
                            <div class="col-md-6">
                                <label class="form-label">Chi vuoi contattare? (Azienda o Settore)</label>
                                <input type="text" id="targetInfo" class="form-control" placeholder="es. Conad o Lavanderia Lampo">
                            </div>
                            <div class="col-md-6">
                                <label class="form-label">La tua Offerta / Prodotto</label>
                                <input type="text" id="myProduct" class="form-control" placeholder="es. Detersivi ecologici sconto 20%">
                            </div>
                        </div>

                        <button type="button" class="btn btn-primary w-100 mb-3" id="btnGenera" onclick="generaBozzaEmail()">
                            🤖 1. Genera Bozza con IA
                        </button>

                        <div id="emailPreviewArea" style="display: none;" class="p-3 bg-white border rounded">
                            <div class="mb-3">
                                <label class="form-label"><strong>Dominio Web Aziendale Target:</strong></label>
                                <div class="input-group">
                                    <input type="text" id="targetDomain" class="form-control" placeholder="es. lavanderialampo.it">
                                    <button class="btn btn-outline-primary" type="button" onclick="cercaEmailDominio()">🔍 Cerca Email Dominio</button>
                                </div>
                                <div id="foundEmailsCount" class="form-text mt-2"></div>
                                <div id="emailsListContainer" class="mt-2 p-2 bg-light border rounded" style="display: none; max-height: 150px; overflow-y: auto;">
                                    <small class="text-muted fw-bold mb-1 d-block">Indirizzi email trovati:</small>
                                    <ul id="emailsList" class="mb-0 ps-3 small text-secondary"></ul>
                                </div>
                            </div>

                            <div class="mb-3">
                                <label class="form-label"><strong>Oggetto Email:</strong></label>
                                <input type="text" id="emailSubject" class="form-control">
                            </div>
                            <div class="mb-3">
                                <label class="form-label"><strong>Testo Email (Modificabile):</strong></label>
                                <textarea id="emailBody" class="form-control" rows="7"></textarea>
                            </div>

                            <div class="d-flex gap-2">
                                <button type="button" class="btn btn-outline-secondary w-50" onclick="generaBozzaEmail()">🔄 Rigenera Bozza</button>
                                <button type="button" class="btn btn-success w-50" id="btnInviaAuto" onclick="avviaCampagnaAutomatica()">🚀 2. Avvia Bot Invio su Dominio</button>
                            </div>
                            <div id="statusMessage" class="mt-2 text-center"></div>
                        </div>
                    </div>
                </div>

            </div>
        </div>
    </div>

    <script>
    async function generaBozzaEmail() {
        const targetEl = document.getElementById("targetInfo");
        const productEl = document.getElementById("myProduct");
        const btnGenera = document.getElementById("btnGenera");

        if (!targetEl || !productEl) {
            alert("Errore: Impossibile trovare i campi di testo.");
            return;
        }

        const target = targetEl.value.trim();
        const product = productEl.value.trim();

        if(!target || !product) {
            alert("Per favore, compila sia il destinatario che la tua offerta!");
            return;
        }

        btnGenera.disabled = true;
        btnGenera.innerText = "⏳ Generazione bozza e ricerca dominio in corso...";

        try {
            const res = await fetch("/api/generate-email-draft", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    target_info: target,
                    offerta_azienda: product
                })
            });

            const data = await res.json();

            if(data.success) {
                document.getElementById("emailSubject").value = data.subject;
                document.getElementById("emailBody").value = data.body;
                
                if (data.domain) {
                    document.getElementById("targetDomain").value = data.domain;
                }

                document.getElementById("emailPreviewArea").style.display = "block";
            } else {
                alert("Errore IA: " + (data.error || "Impossibile generare la bozza"));
            }
        } catch(e) {
            alert("Errore di connessione con il server.");
        } finally {
            btnGenera.disabled = false;
            btnGenera.innerText = "🤖 1. Genera Bozza con IA";
        }
    }

    async function cercaEmailDominio() {
        const domain = document.getElementById("targetDomain").value.trim();
        const countDiv = document.getElementById("foundEmailsCount");
        const listContainer = document.getElementById("emailsListContainer");
        const listUl = document.getElementById("emailsList");

        if(!domain) {
            alert("Inserisci un dominio valido!");
            return;
        }

        countDiv.innerHTML = '<span class="text-info">🔍 Ricerca indirizzi email in corso...</span>';
        listContainer.style.display = "none";
        listUl.innerHTML = "";

        try {
            const res = await fetch("/api/find-domain-emails", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ domain: domain })
            });

            const data = await res.json();
            if(data.success && data.count > 0) {
                countDiv.innerHTML = `<span class="text-success" style="cursor: pointer;" onclick="toggleEmailList()">✅ Trovate <strong>${data.count}</strong> email pubbliche per ${domain}! <span class="text-decoration-underline">(clicca per mostrare/nascondere)</span></span>`;
                
                data.emails.forEach(email => {
                    const li = document.createElement("li");
                    li.textContent = email;
                    listUl.appendChild(li);
                });
            } else {
                countDiv.innerHTML = `<span class="text-warning">⚠️ Nessuna email trovata direttamente per il dominio ${domain}.</span>`;
            }
        } catch(e) {
            countDiv.innerHTML = '<span class="text-danger">Errore durante la ricerca delle email.</span>';
        }
    }

    function toggleEmailList() {
        const listContainer = document.getElementById("emailsListContainer");
        if (listContainer.style.display === "none") {
            listContainer.style.display = "block";
        } else {
            listContainer.style.display = "none";
        }
    }

    async function avviaCampagnaAutomatica() {
        const domain = document.getElementById("targetDomain").value.trim();
        const subject = document.getElementById("emailSubject").value;
        const body = document.getElementById("emailBody").value;
        const btnInvia = document.getElementById("btnInviaAuto");
        const statusMsg = document.getElementById("statusMessage");

        if(!domain) {
            alert("Inserisci prima il dominio target!");
            return;
        }

        btnInvia.disabled = true;
        statusMsg.innerHTML = '<span class="text-info">⏳ Avvio bot e invio massivo in corso...</span>';

        try {
            const res = await fetch("/api/send-auto-domain-campaign", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    domain: domain,
                    subject: subject,
                    body: body
                })
            });

            const data = await res.json();
            if(res.ok) {
                statusMsg.innerHTML = `<span class="text-success">✅ ${data.message}</span>`;
            } else {
                statusMsg.innerHTML = `<span class="text-danger">❌ Errore: ${data.detail || 'Impossibile avviare la campagna.'}</span>`;
            }
        } catch(e) {
            statusMsg.innerHTML = '<span class="text-danger">❌ Errore di connessione con il server.</span>';
        } finally {
            btnInvia.disabled = false;
        }
    }
    </script>
</body>
</html>
"""

def get_dashboard_routes(get_db_func, AziendaModel, SlotAgendaModel):
    @router.get("/{azienda_id}", response_class=HTMLResponse)
    def show_dashboard(azienda_id: int, db: Session = Depends(get_db_func)):
        azienda = db.query(AziendaModel).filter(AziendaModel.id == azienda_id).first()
        if not azienda:
            return HTMLResponse(content="Azienda non trovata", status_code=404)

        appuntamenti = db.query(SlotAgendaModel).filter(
            SlotAgendaModel.azienda_id == azienda_id,
            SlotAgendaModel.stato == "Occupato"
        ).all()

        template = Template(HTML_TEMPLATE)
        return HTMLResponse(content=template.render(azienda=azienda, appuntamenti=appuntamenti))

    @router.post("/{azienda_id}/update-prompt")
    def update_prompt(azienda_id: int, nome: str = Form(...), istruzioni_ia: str = Form(...), db: Session = Depends(get_db_func)):
        azienda = db.query(AziendaModel).filter(AziendaModel.id == azienda_id).first()
        if azienda:
            azienda.nome = nome
            azienda.istruzioni_ia = istruzioni_ia
            db.commit()
        return RedirectResponse(url=f"/dashboard/{azienda_id}", status_code=303)

    @router.post("/{azienda_id}/delete-slot/{slot_id}")
    def delete_slot(azienda_id: int, slot_id: int, db: Session = Depends(get_db_func)):
        slot = db.query(SlotAgendaModel).filter(SlotAgendaModel.id == slot_id, SlotAgendaModel.azienda_id == azienda_id).first()
        if slot:
            db.delete(slot)
            db.commit()
        return RedirectResponse(url=f"/dashboard/{azienda_id}", status_code=303)

    return router
