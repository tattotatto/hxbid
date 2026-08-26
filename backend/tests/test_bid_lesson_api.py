"""bid-lessons 路由注册 sanity 测试."""

from app.api.router import api_router


def test_bid_lessons_routes_registered():
    paths = {route.path for route in api_router.routes}
    assert "/bid-lessons/upload" in paths
    assert "/bid-lessons/{id}" in paths
    assert "/bid-lessons/{id}/relearn" in paths
