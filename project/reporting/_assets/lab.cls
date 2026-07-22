% lab.cls — shared LaTeX class for the article-based artifacts: papers / posters /
% writeups. (Beamer slides bring their OWN class and do NOT load lab.cls; they share
% only the font policy in fonts.tex and the generated theme colours.)
% Loads the generated theme colours so documents match the figures.
% Extend this (fonts, margins, title block) to taste; keep it the single style
% source so every article-based artifact stays consistent.
\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{lab}[2026/01/01 Lab shared class]

\DeclareOption*{\PassOptionsToClass{\CurrentOption}{article}}
\ProcessOptions\relax
\LoadClass{article}

% Font policy is SHARED with the Beamer deck — see reporting/_assets/fonts.tex
% (Latin + CJK via Noto; XeLaTeX/LuaLaTeX; other scripts need a font added there).
% Resolved via TEXINPUTS=reporting/_assets: (reporting/Makefile).
\input{fonts.tex}

\RequirePackage[dvipsnames]{xcolor}
\RequirePackage{graphicx}

% Generated from conf/theme (light/print colours). Do not edit that file.
% Resolved via TEXINPUTS (build from repo root: TEXINPUTS=reporting/_assets: ...),
% see reporting/Makefile.
\IfFileExists{theme-colors.tex}{\input{theme-colors.tex}}{}

\endinput
