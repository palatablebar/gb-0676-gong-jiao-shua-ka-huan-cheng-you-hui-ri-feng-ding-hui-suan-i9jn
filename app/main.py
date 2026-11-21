from flask import Flask, jsonify, request
from app.config import MODULES
from app.errors import DomainError
from app import pipeline, store, rule_registry
from app.web import register_pages
from app.attribution import compare
import importlib


def create_app():
    application = Flask(__name__, template_folder="templates", static_folder="static")
    application.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024

    @application.errorhandler(DomainError)
    def domain_error(error):
        return jsonify({"code": error.code, "message": error.message}), error.status

    @application.errorhandler(KeyError)
    @application.errorhandler(TypeError)
    @application.errorhandler(ValueError)
    def invalid_shape(error):
        return jsonify({"code": "INVALID_INPUT", "message": "字段或参数无效，请核对输入"}), 422

    @application.errorhandler(413)
    def too_large(error):
        return jsonify({"code": "TOO_LARGE", "message": "文件超过4MB"}), 413

    @application.get("/healthz")
    def health():
        return {"status": "ok"}

    # ------------------------------------------------------------- 规则登记簿
    @application.get("/api/rules")
    def rules_catalog():
        return {"current": rule_registry.current(), "versions": rule_registry.all_versions()}

    # ------------------------------------------------------------- 计算/批次
    @application.post("/api/runs")
    def runs():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise DomainError("BAD_BODY", "请求需要对象")
        params = payload.get("parameters", {})
        if not isinstance(params, dict):
            raise DomainError("BAD_PARAMETERS", "参数需要对象")
        raw = payload.get("csv")
        if raw is not None and not isinstance(raw, str):
            raise DomainError("BAD_CSV", "CSV需要文本")
        data = raw.encode() if raw is not None else pipeline.load_sample(payload.get("sample", "dirty.csv"))
        batch_ref = payload.get("batch_ref")
        return pipeline.execute(
            data, params, batch_ref=batch_ref,
            kind=payload.get("kind", "复算"), change_note=payload.get("change_note", ""),
        )

    @application.post("/api/upload")
    def upload():
        file = request.files.get("file")
        if not file:
            raise DomainError("NO_FILE", "请选择CSV文件")
        return pipeline.execute(file.read())

    @application.get("/api/runs")
    def history():
        return {"runs": store.history()}

    @application.get("/api/runs/<run_id>")
    def run_detail(run_id):
        return store.get(run_id)

    @application.post("/api/runs/<run_id>/review")
    def review(run_id):
        p = request.get_json(silent=True) or {}
        if not isinstance(p.get("version"), int):
            raise DomainError("BAD_VERSION", "请提供版本号")
        return store.review(run_id, p["version"], p.get("decision"))

    @application.get("/api/runs/<run_id>/archive")
    def archive(run_id):
        from app.archive import build_archive
        run = store.get(run_id)
        return application.response_class(
            build_archive(run),
            mimetype="text/csv",
            headers={"Content-Disposition": 'attachment; filename="evidence.csv"'},
        )

    # ------------------------------------------------- 历史批次复算（新能力）
    @application.post("/api/batches/<batch_ref>/recalc")
    def batch_recalc(batch_ref):
        """同一业务批次重出。同输入同规则重试不新增版本；新输入或新规则追加版本。"""
        body = request.get_json(silent=True) or {}
        params = body.get("parameters", {})
        if not isinstance(params, dict):
            raise DomainError("BAD_PARAMETERS", "参数需要对象")
        raw = body.get("csv")
        if raw is not None:
            if not isinstance(raw, str):
                raise DomainError("BAD_CSV", "CSV需要文本")
            data = raw.encode()
        else:
            data = pipeline.load_sample(body.get("sample", "dirty.csv"))
        return pipeline.recalc(
            batch_ref, data, params,
            kind=body.get("kind", "复算"), change_note=body.get("change_note", ""),
        )

    @application.get("/api/batches")
    def batches():
        return {"batches": store.list_batches()}

    @application.get("/api/batches/<batch_ref>")
    def batch_detail(batch_ref):
        return store.get_batch(batch_ref)

    @application.get("/api/batches/<batch_ref>/compare")
    def batch_compare(batch_ref):
        """沿同一来源对照原账、复算账与变化原因。"""
        batch = store.get_batch(batch_ref)
        original = store.get(batch["versions"][0]["run_id"])
        current = store.get(batch["current_run_id"])
        return {"batch": batch_ref, "comparison": compare(original, current)}

    @application.post("/api/batches/<batch_ref>/switch")
    def batch_switch(batch_ref):
        body = request.get_json(silent=True) or {}
        run_id = body.get("run_id")
        if not run_id:
            raise DomainError("BAD_RUN", "请提供要切换的 run_id")
        return store.switch_current(batch_ref, run_id, body.get("expected_run_id"))

    @application.post("/api/batches/<batch_ref>/seal")
    def batch_seal(batch_ref):
        return store.seal(batch_ref)

    @application.get("/api/batches/<batch_ref>/verify")
    def batch_verify(batch_ref):
        return store.verify_chain(batch_ref)

    for index, (slug, title) in enumerate(MODULES, 1):
        module = importlib.import_module(f"app.modules.m{index}_{slug}.handler")
        application.register_blueprint(module.bp)
    register_pages(application)
    return application


app = create_app()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
