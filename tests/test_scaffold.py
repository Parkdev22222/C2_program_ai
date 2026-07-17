def test_c2_package_importable():
    import c2  # noqa: F401
    import c2.domain.wargame  # noqa: F401
    import c2.application.ports  # noqa: F401
    import c2.infrastructure  # noqa: F401
    import c2.presentation  # noqa: F401
