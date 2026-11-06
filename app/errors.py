from flask import Flask, render_template


def register_error_handlers(app: Flask):
    @app.errorhandler(400)
    def bad_request(_error):
        return render_template("errors/400.html"), 400

    @app.errorhandler(404)
    def page_not_found(_error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def internal_server_error(_error):
        return render_template("errors/500.html"), 500
