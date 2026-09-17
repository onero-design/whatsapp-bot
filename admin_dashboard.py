from datetime import datetime
from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from jinja2 import Template

def get_admin_routes(get_db, Azienda, Utente, genera_hash_password):
    router = APIRouter()

    @router.get("/admin/super-dashboard", response_class=HTMLResponse)
    def super_dashboard(request: Request, db: Session = Depends(get_db)):
        utenti = db.query(Utente).all()
        
        html_content = """
        <!DOCTYPE html>
        <html lang="it">
        <head>
            <title>Super Admin Panel - SaaS</title>
            <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
        </head>
        <body class="bg-light p-4">
            <div class="container bg-white p-4 rounded shadow-sm">
                <div class="d-flex justify-content-between align-items-center mb-4">
                    <h2>Pannello Gestione Clienti SaaS</h2>
                    <a href="/logout" class="btn btn-outline-danger btn-sm">Logout</a>
                </div>
                
                <div class="card mb-4">
                    <div class="card-header bg-primary text-white">Crea Nuovo Cliente B2B</div>
                    <div class="card-body">
                        <form action="/admin/crea-cliente" method="POST" class="row g-3">
                            <div class="col-md-3">
                                <input type="text" name="nome_azienda" class="form-control" placeholder="Nome Azienda / Attività" required>
                            </div>
                            <div class="col-md-3">
                                <input type="email" name="email" class="form-control" placeholder="Email Cliente" required>
                            </div>
                            <div class="col-md-3">
                                <input type="password" name="password" class="form-control" placeholder="Password Iniziale" required>
                            </div>
                            <div class="col-md-3">
                                <button type="submit" class="btn btn-success w-100">+ Crea Cliente</button>
                            </div>
                        </form>
                    </div>
                </div>

                <h4>I Tuoi Clienti Attuali</h4>
                <table class="table table-striped align-middle">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Email</th>
                            <th>Azienda</th>
                            <th>Stato</th>
                            <th>Azioni</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for u in utenti %}
                        <tr>
                            <td>{{ u.id }}</td>
                            <td>{{ u.email }}</td>
                            <td>{{ u.azienda.nome if u.azienda else 'N/A' }}</td>
                            <td>
                                {% if u.is_active %}
                                    <span class="badge bg-success">Attivo</span>
                                {% else %}
                                    <span class="badge bg-danger">Sospeso</span>
                                {% endif %}
                            </td>
                            <td>
                                <form action="/admin/toggle-stato/{{ u.id }}" method="POST" style="display:inline;">
                                    {% if u.is_active %}
                                        <button class="btn btn-warning btn-sm">Disabilita</button>
                                    {% else %}
                                        <button class="btn btn-success btn-sm">Riapri Account</button>
                                    {% endif %}
                                </form>
                                {% if u.azienda_id %}
                                    <a href="/admin/login-as/{{ u.azienda_id }}" class="btn btn-outline-primary btn-sm">Entra come Cliente</a>
                                {% endif %}
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </body>
        </html>
        """
        template = Template(html_content)
        return HTMLResponse(content=template.render(utenti=utenti))

    @router.post("/admin/crea-cliente")
    def crea_cliente(
        nome_azienda: str = Form(...),
        email: str = Form(...),
        password: str = Form(...),
        db: Session = Depends(get_db)
    ):
        # 1. Crea l'azienda
        nuova_azienda = Azienda(
            nome=nome_azienda,
            numero_whatsapp_business=f"whatsapp:+39{datetime.now().strftime('%M%S%f')}",
            istruzioni_ia="Sei un assistente virtuale gentile."
        )
        db.add(nuova_azienda)
        db.commit()
        db.refresh(nuova_azienda)

        # 2. Crea l'utente per il cliente
        nuovo_utente = Utente(
            email=email,
            password_hash=genera_hash_password(password),
            azienda_id=nuova_azienda.id,
            is_active=True,
            is_admin=False
        )
        db.add(nuovo_utente)
        db.commit()

        return RedirectResponse(url="/admin/super-dashboard", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/admin/toggle-stato/{utente_id}")
    def toggle_stato_utente(utente_id: int, db: Session = Depends(get_db)):
        utente = db.query(Utente).filter(Utente.id == utente_id).first()
        if utente:
            utente.is_active = not utente.is_active
            db.commit()
        return RedirectResponse(url="/admin/super-dashboard", status_code=status.HTTP_303_SEE_OTHER)

    return router

@router.get("/admin/login-as/{azienda_id}")
    def login_come_cliente(azienda_id: int, request: Request, db: Session = Depends(get_db)):
        # Trova l'azienda
        azienda = db.query(Azienda).filter(Azienda.id == azienda_id).first()
        if not azienda:
            return RedirectResponse(url="/admin/super-dashboard", status_code=status.HTTP_303_SEE_OTHER)

        # Imposta il cookie per entrare nella dashboard dell'azienda selezionata
        response = RedirectResponse(url=f"/dashboard/{azienda_id}", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(key="azienda_id", value=str(azienda_id), httponly=True)
        return response
