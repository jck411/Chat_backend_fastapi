import psutil
import sys

def kill_process_on_port(port):
    """Kills any process listening on the specified port."""
    found = False
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            for conn in proc.net_connections(kind='inet'):
                if conn.laddr.port == port:
                    print(f"Killing process using port {port}: {proc.info['name']} (PID: {proc.info['pid']})")
                    proc.kill()
                    found = True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    if not found:
        print(f"No process found using port {port}.")

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    kill_process_on_port(port)
