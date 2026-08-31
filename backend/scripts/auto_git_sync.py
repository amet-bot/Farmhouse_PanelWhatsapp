import os
import subprocess
import time
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHECK_INTERVAL_SECONDS = 5
DEBOUNCE_SECONDS = 3

def run_git_cmd(args):
    try:
        res = subprocess.run(
            ["git"] + args,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return 1, "", str(e)

def has_changes():
    code, out, _ = run_git_cmd(["status", "--porcelain"])
    if code == 0 and out:
        # Filtrar solo archivos significativos (ignora no rastreados que esten en .gitignore)
        return bool(out.strip())
    return False

def sync_to_github():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔄 Cambios detectados. Sincronizando con GitHub...")
    
    # 1. git add .
    code, _, err = run_git_cmd(["add", "."])
    if code != 0:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error en 'git add': {err}")
        return False

    # 2. git commit
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    commit_msg = f"auto: sincronización de cambios ({timestamp})"
    code, out, err = run_git_cmd(["commit", "-m", commit_msg])
    if code != 0:
        if "nothing to commit" in out or "nothing to commit" in err:
            return True
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error en 'git commit': {err or out}")
        return False
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 💾 Commit creado: '{commit_msg}'")

    # 3. git push origin main
    code, out, err = run_git_cmd(["push", "origin", "main"])
    if code == 0:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 ¡Cambios subidos exitosamente a GitHub en la rama 'main'!\n")
        return True
    else:
        # Si aún no se ha configurado el remote o no hay internet
        if "fatal: No configured push destination" in err or "remote" in err.lower() or "does not appear to be a git repository" in err:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Advertencia: No hay un repositorio remoto configurado.")
            print(f"    Ejecuta: git remote add origin <URL_DE_TU_REPOSITORIO_GITHUB>\n")
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Intento de push fallido: {err or out}\n")
        return False

def main():
    print("================================================================")
    print(" 🔄 Auto Git Sync - Farmhouse WhatsApp Center")
    print(" Monitoreando cambios en tiempo real...")
    print(" Presiona CTRL+C para detener el sincronizador.")
    print("================================================================\n")

    # Verificar si git está inicializado
    code, _, _ = run_git_cmd(["status"])
    if code != 0:
        print("❌ Error: El directorio actual no es un repositorio Git.")
        sys.exit(1)

    while True:
        try:
            if has_changes():
                # Debounce para esperar que termines de escribir/guardar
                time.sleep(DEBOUNCE_SECONDS)
                if has_changes():
                    sync_to_github()
            time.sleep(CHECK_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print("\n👋 Auto Git Sync detenido por el usuario.")
            break
        except Exception as ex:
            print(f"❌ Error inesperado en el sincronizador: {ex}")
            time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
