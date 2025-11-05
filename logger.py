import datetime

def log(level, message):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        log_message = f"[{ts}] [{level}] {message}"
        print(log_message, flush=True)
    except UnicodeEncodeError:
        # If default encoding fails, try UTF-8
        import sys
        if sys.stdout.encoding != 'utf-8':
            import codecs
            sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
            print(f"[{ts}] [{level}] {message}", flush=True)