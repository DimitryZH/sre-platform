"""Container entrypoint for explicit Evidence Gateway runtime roles."""

from .assembly import RuntimeConfig, serve


def main() -> None:
    serve(RuntimeConfig.from_args())


if __name__ == "__main__":
    main()
