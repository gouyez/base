class Plugin:
    name = "Unnamed"
    group = "chrome"   # or 'api'
    keep_open_after_run = False

    def build_ui(self, parent):
        """Return dict of UI elements (widgets/vars) placed on parent. Optional."""
        return {}

    def run(self, context: dict):
        """Perform action. context contains: email, log, session, service, people_service, ui, search_terms"""
        raise NotImplementedError
