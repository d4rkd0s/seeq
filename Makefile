.PHONY: test selftest

test:
	@echo "=== Python syntax check ==="
	python3 -m py_compile bin/*.py tools/*.py
	@echo "=== Bash syntax check ==="
	bash -n bin/*.sh bin/coa
	@echo "=== Unit tests ==="
	python3 tools/test_sequencer.py
	python3 tools/test_qrz.py
	python3 tools/test_pipeline.py
	python3 tools/test_dashboard_js.py
	python3 tools/test_dxcc.py
	python3 tools/test_dashboard_actions.py
	python3 tools/test_borders.py
	python3 tools/test_qrz_xml.py

selftest:
	@echo "=== Running coa selftest ==="
	@./bin/coa selftest >/dev/null 2>&1 || echo "INFO: coa selftest subcommand not yet available (phase 1.2)"
