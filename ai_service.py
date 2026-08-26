import json
import os
from openai import OpenAI

client = OpenAI()

def genera_bozza_email_b2b(target_info: str, offerta_azienda: str) -> dict:
    prompt = f"""
    Sei un esperto di email marketing B2B.
    
    Obiettivi:
    1. Genera una bozza di email persuasiva, professionale e breve per il seguente target: "{target_info}".
       L'offerta da proporre è: "{offerta_azienda}".
    2. Identifica o ipotizza il dominio web aziendale principale più probabile per "{target_info}" (es. se target è "Conad", il dominio è "conad.it"; se è "Bar Sport", ipotizza un dominio pulito senza spazi o punteggiatura come "barsport.it").

    Rispondi ESCLUSIVAMENTE con un oggetto JSON in questo formato (senza formattazione markdown tipo ```json):
    {{
        "subject": "Oggetto dell'email qui",
        "body": "Testo dell'email qui",
        "suggested_domain": "dominioipotizzato.it"
    }}
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        content = response.choices[0].message.content.strip()
        
        # Pulisce eventuali tag markdown se restituiti dal modello
        if content.startswith("```json"):
            content = content.replace("```json", "").replace("```", "").strip()
            
        data = json.loads(content)
        return {
            "success": True,
            "subject": data.get("subject", ""),
            "body": data.get("body", ""),
            "domain": data.get("suggested_domain", "").lower()
        }
    except Exception as e:
        print(f"Errore generazione IA: {e}")
        return {"success": False, "error": str(e)}
