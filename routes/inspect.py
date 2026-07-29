from ._base import *

bp = Blueprint("inspect", __name__)


@bp.route("/api/profile/<profile_name>/toolsets")
def api_profile_toolsets(profile_name):
    """返回该 profile 的工具集视图：所有注册工具集 + 每平台启用状态"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    registry = _parse_toolsets_registry()
    enabled = _parse_enabled_toolsets(profile_name)
    enabled_flat = set()
    for ts_list in enabled.values():
        enabled_flat.update(ts_list)
    items = [{"name": r["name"], "description": r["description"], "tools": r["tools"],
              "enabled": r["name"] in enabled_flat} for r in registry]
    return jsonify({"toolsets": items, "enabled_by_platform": enabled,
                     "total": len(items), "enabled_count": len(enabled_flat)})


@bp.route("/api/profile/<profile_name>/mcp")
def api_profile_mcp(profile_name):
    """返回该 profile 的 MCP server 列表"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    servers = _parse_mcp_servers(profile_name)
    return jsonify({"mcp_servers": servers, "count": len(servers)})
