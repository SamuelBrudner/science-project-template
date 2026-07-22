# Build reporting artifacts. Run from the repo root:  make -C reporting all
# Uses XeLaTeX; fonts.tex loads Noto Serif/Sans CJK (shared by lab.cls AND the
# Beamer deck), so titles/author names in
# LATIN and CJK typeset. OTHER scripts (Arabic, Devanagari, ...) need their own
# Noto font added to reporting/_assets/fonts.tex (the shared font policy) — a
# missing glyph FAILS the build (below) rather than silently dropping characters.
# Install texlive-xetex + fonts-noto-cjk, or build
# inside a TeX container. Run `snakemake` first so results/figures/ and
# reporting/_assets/theme-colors*.tex exist. TeX is NOT in environment.yml (large).
#
# pdfLaTeX still works for Latin-only documents (lab.cls auto-detects the engine);
# swap TEX below if you prefer it and don't need non-Latin glyphs.
TEX := xelatex
ROOT := $(shell cd .. && pwd)

all: paper slides

# fail_on_missing_glyph <logfile>: XeLaTeX exits 0 even when a glyph is absent, so
# treat any "Missing character" warning as a hard error (fail-loud, not silent).
define fail_on_missing_glyph
	@if grep -q "Missing character" "$(1)"; then \
	  echo "ERROR: the font lacks glyphs for your title/author script. Add a Noto"; \
	  echo "font covering it in reporting/_assets/fonts.tex (the SHARED font policy"; \
	  echo "used by BOTH the paper (lab.cls) and the Beamer deck)."; \
	  exit 1; \
	fi
endef

paper:
	cd $(ROOT) && TEXINPUTS=reporting/_assets: $(TEX) -interaction=nonstopmode \
	  -halt-on-error -output-directory=reporting/papers/example \
	  reporting/papers/example/manuscript.tex
	$(call fail_on_missing_glyph,$(ROOT)/reporting/papers/example/manuscript.log)

slides:
	cd $(ROOT) && TEXINPUTS=reporting/_assets: $(TEX) -interaction=nonstopmode \
	  -halt-on-error -output-directory=reporting/presentations/example \
	  reporting/presentations/example/slides.tex
	$(call fail_on_missing_glyph,$(ROOT)/reporting/presentations/example/slides.log)

.PHONY: all paper slides
