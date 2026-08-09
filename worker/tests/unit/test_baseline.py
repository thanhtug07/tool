def test_baseline_package_importable():
    import src

    assert src.__name__ == "src"
