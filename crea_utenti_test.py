from main import SessionLocal, Azienda, Utente, genera_hash_password

def crea_dati_di_test():
    db = SessionLocal()
    try:
        # 1. Creazione Azienda 1 (Barber Shop)
        barber = db.query(Azienda).filter(Azienda.nome == "Barber Shop Milano").first()
        if not barber:
            barber = Azienda(
                nome="Barber Shop Milano",
                numero_whatsapp_business="whatsapp:+14155238886", # Numero Twilio Sandbox per test
                istruzioni_ia="Sei l'assistente di Barber Shop Milano. Taglio capelli 20€, Barba 10€. Orari: 9:00-19:00."
            )
            db.add(barber)
            db.commit()
            db.refresh(barber)

        # Utente per Azienda 1
        utente1 = db.query(Utente).filter(Utente.email == "barber@test.it").first()
        if not utente1:
            utente1 = Utente(
                email="barber@test.it",
                password_hash=genera_hash_password("password123"),
                azienda_id=barber.id
            )
            db.add(utente1)

        # 2. Creazione Azienda 2 (Centro Estetico)
        estetica = db.query(Azienda).filter(Azienda.nome == "Centro Estetico Bella").first()
        if not estetica:
            estetica = Azienda(
                nome="Centro Estetico Bella",
                numero_whatsapp_business="whatsapp:+390000000000",
                istruzioni_ia="Sei l'assistente del Centro Estetico Bella. Pulizia viso 45€, Manicure 25€. Orari: 10:00-20:00."
            )
            db.add(estetica)
            db.commit()
            db.refresh(estetica)

        # Utente per Azienda 2
        utente2 = db.query(Utente).filter(Utente.email == "estetica@test.it").first()
        if not utente2:
            utente2 = Utente(
                email="estetica@test.it",
                password_hash=genera_hash_password("password123"),
                azienda_id=estetica.id
            )
            db.add(utente2)

        db.commit()
        print("✅ Utenti di test creati con successo!")
        print("-----------------------------------")
        print("Cliente 1 -> Email: barber@test.it | Password: password123")
        print("Cliente 2 -> Email: estetica@test.it | Password: password123")

    except Exception as e:
        print(f"❌ Errore durante la creazione: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    crea_dati_di_test()
