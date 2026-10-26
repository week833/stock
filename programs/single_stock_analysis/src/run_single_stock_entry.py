"""Dispatch a current C0 close-to-close horizon without analyzer module aliases."""
import sys

_HORIZONS = {"H1": 1, "H63": 63}

def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0].upper() not in _HORIZONS:
        print("用法：run_single_stock_entry.py {H1|H63} [其他 C0 參數…]", file=sys.stderr)
        return 2
    horizon = _HORIZONS[args.pop(0).upper()]
    from tw_single_stock_c0 import main as run_c0
    return run_c0(horizon, args)

if __name__ == "__main__":
    raise SystemExit(main())
