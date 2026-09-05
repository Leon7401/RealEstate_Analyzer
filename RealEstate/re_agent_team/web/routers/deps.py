"""ルータ共通依存 — web.app のシングルトンを遅延参照"""

def get_app_module():
    import web.app as app_module
    return app_module


class Lazy:
    """app モジュール属性への遅延プロキシ"""

    def __getattr__(self, name):
        return getattr(get_app_module(), name)


app_deps = Lazy()
