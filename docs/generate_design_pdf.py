#!/usr/bin/env python3
"""Generate EOSAT-1 SMO Code Design Document as PDF using fpdf2."""

from fpdf import FPDF

# ── Colours ──────────────────────────────────────────────────────────
BLUE_DARK  = (26, 35, 126)    # #1a237e
BLUE_MED   = (40, 53, 147)    # #283593
BLUE_LIGHT = (57, 73, 171)    # #3949ab
BLUE_SUB   = (92, 107, 192)   # #5c6bc0
TH_BG      = (232, 234, 246)  # #e8eaf6
CODE_BG    = (245, 245, 245)
ROW_ALT    = (248, 248, 248)
BLACK       = (26, 26, 26)
GRAY        = (100, 100, 100)
WHITE       = (255, 255, 255)


class DesignPDF(FPDF):
    def __init__(self):
        super().__init__(orientation='P', unit='mm', format='Letter')
        # Fonts
        self.add_font('sans', '', '/System/Library/Fonts/Supplemental/Arial Unicode.ttf')
        self.add_font('sans', 'B', '/System/Library/Fonts/Supplemental/Arial Unicode.ttf')
        self.add_font('mono', '', '/System/Library/Fonts/SFNSMono.ttf')
        self.set_auto_page_break(auto=True, margin=18)
        self.alias_nb_pages()
        self._in_cover = False

    def header(self):
        if self._in_cover:
            return
        self.set_font('sans', '', 7)
        self.set_text_color(*GRAY)
        self.set_y(5)
        self.cell(0, 4, 'EOSAT-1 SMO Suite \u2014 Code Design Document', align='C')

    def footer(self):
        if self._in_cover:
            return
        self.set_y(-12)
        self.set_font('sans', '', 7)
        self.set_text_color(*GRAY)
        self.cell(0, 4, f'Page {self.page_no()} of {{nb}}', align='R')

    # ── helpers ───────────────────────────────────────────────────────
    def _ensure_space(self, h=30):
        if self.get_y() > 265 - h:
            self.add_page()

    def section_header(self, text):
        self.add_page()
        self.set_font('sans', 'B', 18)
        self.set_text_color(*BLUE_DARK)
        self.cell(0, 10, text, new_x='LMARGIN', new_y='NEXT')
        self.set_draw_color(*BLUE_DARK)
        self.set_line_width(0.6)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def sub_header(self, text):
        self._ensure_space(14)
        self.set_font('sans', 'B', 13)
        self.set_text_color(*BLUE_MED)
        self.cell(0, 8, text, new_x='LMARGIN', new_y='NEXT')
        self.set_draw_color(197, 202, 233)
        self.set_line_width(0.3)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)

    def sub3_header(self, text):
        self._ensure_space(12)
        self.set_font('sans', 'B', 11)
        self.set_text_color(*BLUE_LIGHT)
        self.cell(0, 7, text, new_x='LMARGIN', new_y='NEXT')
        self.ln(1)

    def sub4_header(self, text):
        self._ensure_space(10)
        self.set_font('sans', 'B', 9.5)
        self.set_text_color(*BLUE_SUB)
        self.cell(0, 6, text, new_x='LMARGIN', new_y='NEXT')
        self.ln(1)

    def body(self, text):
        self.set_font('sans', '', 9)
        self.set_text_color(*BLACK)
        self.multi_cell(0, 4.5, text)
        self.ln(1.5)

    def bullet_list(self, items):
        self.set_font('sans', '', 9)
        self.set_text_color(*BLACK)
        for item in items:
            self._ensure_space(8)
            self.cell(6, 4.5, '\u2022')
            x = self.get_x()
            w = self.w - self.r_margin - x
            self.multi_cell(w, 4.5, item)
            self.ln(0.5)
        self.ln(1)

    def code_block(self, title, text):
        lines = text.strip().split('\n')
        needed = len(lines) * 3.2 + 14
        self._ensure_space(min(needed, 120))
        if title:
            self.set_font('sans', 'B', 8)
            self.set_text_color(*BLUE_DARK)
            self.cell(0, 5, title, new_x='LMARGIN', new_y='NEXT')
        x0 = self.l_margin
        w = self.w - self.l_margin - self.r_margin
        self.set_font('mono', '', 6.5)
        # estimate height
        line_h = 3.2
        block_h = len(lines) * line_h + 4
        y0 = self.get_y()
        # if block is taller than remaining page, split across pages
        self.set_fill_color(*CODE_BG)
        self.set_draw_color(200, 200, 200)
        # Draw background
        if y0 + block_h < self.h - self.b_margin:
            self.rect(x0, y0, w, block_h, style='DF')
        else:
            # just fill as we go
            pass
        self.set_text_color(50, 50, 50)
        self.set_y(y0 + 2)
        for line in lines:
            if self.get_y() > self.h - self.b_margin - 5:
                self.add_page()
                self.set_font('mono', '', 6.5)
                self.set_text_color(50, 50, 50)
            self.set_x(x0 + 2)
            self.cell(w - 4, line_h, line)
            self.ln(line_h)
        self.ln(3)

    def table(self, headers, rows, col_widths=None):
        usable = self.w - self.l_margin - self.r_margin
        n = len(headers)
        if col_widths is None:
            col_widths = [usable / n] * n
        else:
            total = sum(col_widths)
            col_widths = [c / total * usable for c in col_widths]

        row_h = 5
        # Header
        self._ensure_space(row_h * 2 + 6)
        self.set_font('sans', 'B', 7.5)
        self.set_fill_color(*TH_BG)
        self.set_text_color(*BLUE_DARK)
        self.set_draw_color(197, 202, 233)
        x0 = self.l_margin
        y0 = self.get_y()
        for i, h in enumerate(headers):
            self.set_xy(x0 + sum(col_widths[:i]), y0)
            self.cell(col_widths[i], row_h, h, border=1, fill=True)
        self.ln(row_h)

        # Rows
        self.set_font('sans', '', 7)
        self.set_text_color(*BLACK)
        for ri, row in enumerate(rows):
            # Calculate row height needed
            max_lines = 1
            for ci, cell in enumerate(row):
                cw = col_widths[ci] - 2
                if cw > 0:
                    text_w = self.get_string_width(str(cell))
                    lines_needed = max(1, int(text_w / cw) + 1)
                    max_lines = max(max_lines, lines_needed)
            rh = row_h * max_lines
            self._ensure_space(rh + 2)
            if ri % 2 == 1:
                self.set_fill_color(*ROW_ALT)
            else:
                self.set_fill_color(*WHITE)
            self.set_draw_color(220, 220, 220)
            y0 = self.get_y()
            for ci, cell in enumerate(row):
                self.set_xy(x0 + sum(col_widths[:ci]), y0)
                self.cell(col_widths[ci], rh, str(cell), border=1, fill=True)
            self.ln(rh)
        self.ln(2)


def build_pdf():
    pdf = DesignPDF()

    # ═══════════════════ COVER PAGE ═══════════════════
    pdf._in_cover = True
    pdf.add_page()
    pdf.ln(50)
    pdf.set_font('sans', 'B', 30)
    pdf.set_text_color(13, 71, 161)
    pdf.cell(0, 14, 'EOSAT-1', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(4)
    pdf.set_font('sans', '', 16)
    pdf.set_text_color(55, 71, 79)
    pdf.cell(0, 8, 'Space Mission Operations Suite', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 8, 'Code Design Document', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(6)
    # HR
    cx = pdf.w / 2
    pdf.set_draw_color(*BLUE_DARK)
    pdf.set_line_width(0.8)
    pdf.line(cx - 40, pdf.get_y(), cx + 40, pdf.get_y())
    pdf.ln(10)
    # Metadata
    pdf.set_font('sans', '', 11)
    pdf.set_text_color(84, 110, 122)
    meta = [
        ('Mission', 'EOSAT-1 \u2014 6U Multispectral Ocean Imaging CubeSat'),
        ('Document', 'SMO-DD-001 Rev 1.0'),
        ('Date', '2026-04-01'),
        ('Classification', 'UNCLASSIFIED'),
        ('Authors', 'Generated by AI (Claude Code)'),
    ]
    for label, value in meta:
        pdf.cell(0, 7, f'{label}:  {value}', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(12)
    # AIG Banner
    bw = 80
    bx = (pdf.w - bw) / 2
    pdf.set_fill_color(17, 17, 17)
    pdf.set_text_color(*WHITE)
    pdf.set_font('sans', 'B', 11)
    pdf.set_xy(bx, pdf.get_y())
    pdf.cell(bw, 10, 'AIG \u2014 Artificial Intelligence Generated', fill=True, align='C')
    pdf._in_cover = False

    # ═══════════════════ TABLE OF CONTENTS ═══════════════════
    pdf.add_page()
    pdf.set_font('sans', 'B', 18)
    pdf.set_text_color(*BLUE_DARK)
    pdf.cell(0, 10, 'Table of Contents', new_x='LMARGIN', new_y='NEXT')
    pdf.set_draw_color(*BLUE_DARK)
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(6)
    toc_items = [
        'System Overview & Mission Profile',
        'Systems Architecture',
        'Package & Module Structure',
        'Component Diagram',
        'Use Case Diagrams',
        'Spacecraft Subsystem Models',
        'PUS Service Architecture & Command Flow',
        'Telemetry Processing Pipeline',
        'State Machine Diagrams',
        'Sequence Diagrams',
        'Network Topology & Deployment',
        'MCS User Interface Architecture',
        'Mission Planner Architecture',
        'Configuration & Data Model',
        'Concurrency & Thread Safety',
        'Test Architecture',
        'Class Index',
    ]
    pdf.set_font('sans', '', 10.5)
    pdf.set_text_color(*BLUE_DARK)
    for i, item in enumerate(toc_items, 1):
        pdf.cell(0, 8, f'  {i}.   {item}', new_x='LMARGIN', new_y='NEXT')

    # ═══════════════════ SECTION 1 ═══════════════════
    pdf.section_header('1. System Overview & Mission Profile')

    pdf.sub_header('1.1 Mission Summary')
    pdf.body(
        'EOSAT-1 is a 6U multispectral imaging CubeSat designed for ocean current monitoring. '
        'The spacecraft operates in a 450 km sun-synchronous orbit (98\u00b0 inclination) with a '
        'multispectral camera capturing ocean colour data at 443, 560, 665, and 865 nm wavelengths.'
    )
    pdf.table(
        ['Parameter', 'Value'],
        [
            ['Form Factor', '6U CubeSat'],
            ['Orbit', '450 km circular SSO, 98\u00b0 inclination'],
            ['Period', '~94 min (15.24 rev/day)'],
            ['Ground Stations', 'Iqaluit (63.7\u00b0N, 68.5\u00b0W), Troll (72.0\u00b0S, 2.5\u00b0E)'],
            ['Payload', '4-band multispectral camera (ocean colour)'],
            ['Downlink', 'S-band, 64 kbps high / 1 kbps low rate'],
            ['Power', '6 body-mounted GaAs panels, 120 Wh Li-ion battery'],
            ['Redundancy', 'Cold-redundant: transceiver, PDM, OBC (A/B), magnetometers (A/B)'],
        ],
        col_widths=[30, 70],
    )

    pdf.sub_header('1.2 Suite Composition')
    pdf.body(
        'The Space Mission Operations (SMO) Suite is a monorepo consisting of five Python packages '
        'that together provide a complete spacecraft operations training and simulation environment:'
    )
    pdf.table(
        ['Package', 'Role', 'Port(s)'],
        [
            ['smo-common', 'Shared library: ECSS protocol, orbit propagation, config schemas', '\u2014'],
            ['smo-simulator', 'Spacecraft simulation engine with 6 subsystem models', 'TCP 8001/8002, HTTP 8080'],
            ['smo-mcs', 'Mission Control System: telemetry display, commanding, procedures', 'HTTP 9090'],
            ['smo-planner', 'Mission planning: scheduling, power/data budgets, imaging', 'HTTP 9091'],
            ['smo-gateway', 'Optional TM/TC relay for distributed deployments', 'TCP 10025'],
        ],
        col_widths=[22, 58, 20],
    )

    # ═══════════════════ SECTION 2 ═══════════════════
    pdf.section_header('2. Systems Architecture')

    pdf.sub_header('2.1 High-Level Architecture Diagram')
    pdf.body(
        'The architecture follows a classical ground-segment model with three regions: '
        'Spacecraft Simulator, Ground Segment servers, and Operator Interfaces (browsers).'
    )
    pdf.body(
        'SPACECRAFT SIMULATOR: Contains SimulationEngine with 6 subsystem models (EPS, AOCS, TCS, OBDH, TTC, Payload), '
        'ServiceDispatcher (PUS S1-S20), TCScheduler, TMStorage, FailureManager, OrbitPropagator (SGP4), '
        'ScenarioEngine, BreakpointManager, TMBuilder, FDIR, and Phase State Machine. '
        'Exposes TC Server (TCP :8001), TM Server (TCP :8002), and Instructor UI (HTTP/WS :8080).'
    )
    pdf.body(
        'GROUND SEGMENT: MCS Server (HTTP/WS :9090) with TCManager, TMProcessor, TMArchive (SQLite 7-day), '
        'ProcedureRunner, AlarmJournal, GO/NO-GO, Position RBAC, DisplayEngine. '
        'Planner Server (HTTP :9091) with OrbitPlanner, ActivityScheduler, BudgetTracker, ImagingPlanner, ContactPlanner. '
        'smo-common shared library (ECSS Protocol, Framing, OrbitPropagator, Config Schemas, TMBuilder, ParameterRegistry). '
        'Optional Gateway relay (TCP :10025).'
    )
    pdf.body(
        'OPERATOR INTERFACES: MCS Operator Display (up to 30 users), Planner Display (up to 10 users), '
        'Instructor Console (1 sim operator). All browser-based, connected via WebSocket and HTTP.'
    )

    pdf.sub_header('2.2 Data Flow Summary')
    pdf.code_block('Telecommand (TC) Flow \u2014 Operator to Spacecraft',
        'Browser \u2500\u2500WebSocket\u2500\u2500\u25b6 MCS Server \u2500\u2500TCP :8001\u2500\u2500\u25b6 Simulator Engine\n'
        '  |                       |                          |\n'
        '  | POST /api/pus-command  | TCManager.build_command() | _dispatch_tc()\n'
        '  | {service,subtype,data} | frame_packet()           | ServiceDispatcher.dispatch()\n'
        '  |                       | _tc_send_lock (serialize) | -> S1.1 accept / S1.2 reject\n'
        '  |                       |                          | -> execute -> S1.7 / S1.8\n'
        '  |                       |<--- S1 Verification TM --|\n'
        '  |<-- verification_log --|                          |'
    )
    pdf.code_block('Telemetry (TM) Flow \u2014 Spacecraft to Operator',
        'Simulator Engine \u2500\u2500TCP :8002\u2500\u2500\u25b6 MCS Server \u2500\u2500WebSocket\u2500\u2500\u25b6 Browser\n'
        '  |                               |                        |\n'
        '  | TMBuilder._pack_tm()          | decommutate_packet()   | state update\n'
        '  | S3 HK, S5 events, S1 verif   | TMProcessor._process() | chart push\n'
        '  | enqueue to tm_queue           | TMArchive.store()      | alarm journal\n'
        '  |                               | limit check -> alarm   | event log\n'
        '  |                               | broadcast to 30 WS     |'
    )

    # ═══════════════════ SECTION 3 ═══════════════════
    pdf.section_header('3. Package & Module Structure')

    pdf.sub_header('3.1 smo-common \u2014 Shared Infrastructure')
    pdf.code_block('Module Map',
        'smo_common/\n'
        '\u251c\u2500\u2500 config/\n'
        '\u2502   \u251c\u2500\u2500 schemas.py          29 Pydantic models (MissionConfig, OrbitConfig, EPSConfig, ...)\n'
        '\u2502   \u2514\u2500\u2500 loader.py           16 YAML config loaders\n'
        '\u251c\u2500\u2500 models/\n'
        '\u2502   \u251c\u2500\u2500 subsystem.py        SubsystemModel ABC (8 abstract methods)\n'
        '\u2502   \u2514\u2500\u2500 registry.py         Plugin registry (discover_models, create_model)\n'
        '\u251c\u2500\u2500 orbit/\n'
        '\u2502   \u251c\u2500\u2500 propagator.py       OrbitPropagator (SGP4), OrbitState, GroundStation\n'
        '\u2502   \u251c\u2500\u2500 eclipse.py          Cylindrical shadow model\n'
        '\u2502   \u2514\u2500\u2500 contacts.py         Contact window computation\n'
        '\u251c\u2500\u2500 protocol/\n'
        '\u2502   \u251c\u2500\u2500 ecss_packet.py      CCSDS/ECSS packet build/parse, CRC-16, PUS enums\n'
        '\u2502   \u251c\u2500\u2500 framing.py          TCP length-prefix framing (2-byte header)\n'
        '\u2502   \u2514\u2500\u2500 pus_services.py     S1/S3/S5/S20 data parsers\n'
        '\u2514\u2500\u2500 telemetry/\n'
        '    \u251c\u2500\u2500 parameters.py       ParameterInfo, ParameterRegistry\n'
        '    \u2514\u2500\u2500 tm_builder.py       TMBuilder (HK, events, verification, time)'
    )

    pdf.sub_header('3.2 smo-simulator \u2014 Spacecraft Simulator')
    pdf.code_block('Module Map',
        'smo_simulator/\n'
        '\u251c\u2500\u2500 engine.py               SimulationEngine \u2014 main tick loop, phase SM, HK emission\n'
        '\u251c\u2500\u2500 server.py               TCP TC/TM servers + instructor HTTP\n'
        '\u251c\u2500\u2500 service_dispatch.py     ServiceDispatcher \u2014 PUS S1-S20 command routing\n'
        '\u251c\u2500\u2500 tc_scheduler.py         TCScheduler (S11) \u2014 time-tagged command queue\n'
        '\u251c\u2500\u2500 tm_storage.py           OnboardTMStorage (S15) \u2014 4 circular/linear stores\n'
        '\u251c\u2500\u2500 failure_manager.py      FailureManager \u2014 step/gradual/intermittent injection\n'
        '\u251c\u2500\u2500 scenario_engine.py      ScenarioEngine \u2014 YAML scenario execution & scoring\n'
        '\u251c\u2500\u2500 breakpoints.py          BreakpointManager \u2014 state save/restore\n'
        '\u251c\u2500\u2500 fdir.py                 FDIR helper \u2014 rule evaluation & action dispatch\n'
        '\u251c\u2500\u2500 instructor/\n'
        '\u2502   \u251c\u2500\u2500 app.py              aiohttp app \u2014 /api/state, /api/command, /api/scenarios\n'
        '\u2502   \u2514\u2500\u2500 static/index.html   Instructor web console\n'
        '\u2514\u2500\u2500 models/\n'
        '    \u251c\u2500\u2500 eps_basic.py        EPSBasicModel \u2014 solar, battery, power lines, OC protection\n'
        '    \u251c\u2500\u2500 aocs_basic.py       AOCSBasicModel \u2014 attitude, RW, ST, CSS, MAG, MTQ\n'
        '    \u251c\u2500\u2500 tcs_basic.py        TCSBasicModel \u2014 zones, heaters, cooler, thermostat\n'
        '    \u251c\u2500\u2500 obdh_basic.py       OBDHBasicModel \u2014 OBC A/B, CAN bus, bootloader, watchdog\n'
        '    \u251c\u2500\u2500 ttc_basic.py        TTCBasicModel \u2014 link budget, PA, BER, lock acquisition\n'
        '    \u2514\u2500\u2500 payload_basic.py    PayloadBasicModel \u2014 multispectral, FPA, image catalog'
    )

    pdf.sub_header('3.3 smo-mcs \u2014 Mission Control System')
    pdf.code_block('Module Map',
        'smo_mcs/\n'
        '\u251c\u2500\u2500 server.py               MCSServer \u2014 HTTP/WS, 40+ API endpoints, alarm journal\n'
        '\u251c\u2500\u2500 tc_manager.py           TCManager \u2014 PUS packet builder, seq counter, verification\n'
        '\u251c\u2500\u2500 tm_processor.py         TMProcessor \u2014 HK decommutation, limit checks, history\n'
        '\u251c\u2500\u2500 tm_archive.py           TMArchive \u2014 SQLite (params, events, alarms, commands)\n'
        '\u251c\u2500\u2500 procedure_runner.py     ProcedureRunner \u2014 step execution, wait/verify/override\n'
        '\u251c\u2500\u2500 displays/\n'
        '\u2502   \u251c\u2500\u2500 engine.py           DisplayEngine \u2014 YAML-driven widget rendering\n'
        '\u2502   \u2514\u2500\u2500 widgets.py          Gauge, LineChart, ValueTable, StatusIndicator, EventLog\n'
        '\u2514\u2500\u2500 static/\n'
        '    \u2514\u2500\u2500 index.html          11-tab operator UI (~5800 lines), 17 Chart.js instances'
    )

    pdf.sub_header('3.4 smo-planner \u2014 Mission Planner')
    pdf.code_block('Module Map',
        'smo_planner/\n'
        '\u251c\u2500\u2500 server.py               PlannerServer \u2014 23 HTTP endpoints, orbit recompute loop\n'
        '\u251c\u2500\u2500 orbit_planner.py        OrbitPlanner \u2014 ground track prediction\n'
        '\u251c\u2500\u2500 contact_planner.py      ContactPlanner \u2014 multi-station pass computation\n'
        '\u251c\u2500\u2500 activity_scheduler.py   ActivityScheduler \u2014 CRUD, conflict detection, pass-based\n'
        '\u251c\u2500\u2500 budget_tracker.py       BudgetTracker \u2014 SoC prediction, data volume, link budget\n'
        '\u251c\u2500\u2500 imaging_planner.py      ImagingPlanner \u2014 7 ocean targets, swath opportunities\n'
        '\u251c\u2500\u2500 utils.py                ISO datetime parsing\n'
        '\u2514\u2500\u2500 static/\n'
        '    \u251c\u2500\u2500 index.html           Single-monitor planner UI\n'
        '    \u2514\u2500\u2500 index-wide.html      5760x1080 wide display'
    )

    pdf.sub_header('3.5 smo-gateway \u2014 TM/TC Relay')
    pdf.code_block('Module Map',
        'smo_gateway/\n'
        '\u251c\u2500\u2500 gateway.py              Gateway \u2014 bidirectional TM/TC relay\n'
        '\u251c\u2500\u2500 upstream.py             UpstreamConnection \u2014 single upstream link\n'
        '\u2514\u2500\u2500 downstream.py           DownstreamManager \u2014 multi-client broadcast'
    )

    # ═══════════════════ SECTION 4 ═══════════════════
    pdf.section_header('4. Component Diagram')
    pdf.body(
        'The component diagram shows internal structure of each package and their inter-package dependencies. '
        'Solid arrows represent runtime data flow (TC/TM); dashed arrows represent library dependencies on smo-common.'
    )

    pdf.sub_header('4.1 smo-simulator Components')
    pdf.body(
        'SimulationEngine and ServiceDispatcher are the two top-level components. The engine orchestrates '
        '6 subsystem models (EPS, AOCS, TCS, OBDH, TTC, Payload), plus TCScheduler, TMStorage, '
        'OrbitPropagator, FailureManager, ScenarioEngine, TMBuilder, and FDIR Engine. '
        'Three network interfaces are exposed: TC :8001, TM :8002, and HTTP :8080 (Instructor).'
    )

    pdf.sub_header('4.2 smo-mcs Components')
    pdf.body(
        'MCSServer, TCManager, and TMProcessor form the top layer. Supporting components include '
        'ProcedureRunner, TMArchive, AlarmJournal, GO/NO-GO, Position RBAC, and DisplayEngine. '
        'The combined HTTP/WS :9090 interface serves the 11-tab UI with 17 charts, alarm modal, and procedure controls.'
    )

    pdf.sub_header('4.3 smo-planner Components')
    pdf.body(
        'OrbitPlanner, ActivityScheduler, BudgetTracker, ImagingPlanner, ContactPlanner, and SVG WorldMap. '
        'HTTP :9091 exposes schedule, budget, ground track, and imaging endpoints.'
    )

    pdf.sub_header('4.4 smo-common Library')
    pdf.body(
        'Shared components used by all packages: ECSS Protocol, TCP Framing, OrbitPropagator, '
        'Config Schemas, TMBuilder, PUS Enums, and Registry.'
    )

    pdf.sub_header('4.5 smo-gateway (Optional)')
    pdf.body(
        'Upstream-Downstream relay on TCP :10025 for multi-site deployments.'
    )

    # ═══════════════════ SECTION 5 ═══════════════════
    pdf.section_header('5. Use Case Diagrams')

    pdf.sub_header('5.1 Operator Use Cases (MCS)')
    pdf.body(
        'Three actor types interact with the Mission Control System: Flight Director, Subsystem Engineer, and Sim Instructor.'
    )

    pdf.sub3_header('Flight Director Use Cases')
    pdf.bullet_list([
        'Monitor Telemetry \u2014 real-time HK data from all subsystems',
        'View Alarm Journal \u2014 S5/S12 events with severity badges',
        'Send PUS Commands \u2014 full access to all services and function IDs',
        'Initiate GO/NO-GO Poll \u2014 flight director exclusive privilege',
    ])

    pdf.sub3_header('Subsystem Engineer Use Cases')
    pdf.bullet_list([
        'Monitor Telemetry \u2014 subsystem-filtered view',
        'Send PUS Commands \u2014 restricted to assigned subsystem function IDs',
        'Acknowledge Alarms \u2014 mark alarms as acknowledged',
        'Query TM Archive \u2014 historical telemetry retrieval',
        'Respond to GO/NO-GO \u2014 per-position GO/NO-GO vote',
        'TM Dump Playback (S15) \u2014 stored TM retrieval and display',
        'Shift Handover \u2014 handover notes with timestamps',
        'Build Custom Procedure \u2014 procedure creation and editing',
    ])

    pdf.sub3_header('Sim Instructor Use Cases')
    pdf.bullet_list([
        'Inject Failure \u2014 step/gradual/intermittent failure injection via FailureManager',
        'Start/Load Scenario \u2014 YAML scenario execution and scoring',
        'Control Sim (speed/freeze) \u2014 simulation time control',
    ])

    # ═══════════════════ SECTION 6 ═══════════════════
    pdf.section_header('6. Spacecraft Subsystem Models')

    pdf.sub_header('6.1 Subsystem Model Class Hierarchy')
    pdf.code_block('Inheritance',
        '                    <<ABC>> SubsystemModel\n'
        '                    ----------------------\n'
        '                    + name: str\n'
        '                    + configure(config)\n'
        '                    + tick(dt, orbit, params)\n'
        '                    + handle_command(cmd)\n'
        '                    + inject_failure(failure, mag)\n'
        '                    + clear_failure(failure)\n'
        '                    + get_state() / set_state()\n'
        '                            |\n'
        '        +-------+-------+---+---+-------+-------+\n'
        '        v       v       v       v       v       v\n'
        '   EPSBasic AOCSBasic TCSBasic OBDHBasic TTCBasic PayloadBasic'
    )

    pdf.sub_header('6.2 Subsystem Parameter ID Ranges')
    pdf.table(
        ['Subsystem', 'ID Range', 'Key Parameters', 'Count'],
        [
            ['EPS', '0x0100\u20130x0130', 'bat_soc, bat_voltage, sa_current, bus_voltage, power_gen/cons, per-panel currents', '~45'],
            ['AOCS', '0x0200\u20130x0264', 'quaternion, rates, rw_speeds, mag_xyz, css_xyz, st_status, att_error, mode', '~40'],
            ['OBDH', '0x0300\u20130x0318', 'cpu_load, temp, uptime, tc/tm_counts, cuc_time, sw_image, bus_status', '~25'],
            ['TCS', '0x0400\u2013\u200b0x040D', 'panel_temps(6), obc_temp, bat_temp, fpa_temp, heater_states, cooler', '~15'],
            ['TTC', '0x0500\u20130x0519', 'rssi, ber, eb_n0, tm_rate, pa_temp, lock_state, doppler, cmd_count', '~20'],
            ['Payload', '0x0600\u20130x0613', 'mode, fpa_temp, store_used, image_count, band_snrs, gsd, swath', '~20'],
        ],
        col_widths=[14, 18, 52, 10],
    )

    pdf.sub_header('6.3 Subsystem Feature Matrix')
    pdf.table(
        ['Feature', 'EPS', 'AOCS', 'TCS', 'OBDH', 'TTC', 'Payload'],
        [
            ['State machine', '\u2014', '9 modes', '\u2014', '3 modes', '2 units', '3 modes'],
            ['Redundancy', 'Dual SA', 'Dual ST, dual MAG', '\u2014', 'OBC A/B, Bus A/B', 'Prim/Red XPDR', '\u2014'],
            ['Sensors', '\u2014', '2xST, 6xCSS, 2xMAG, GPS', 'Thermal', '\u2014', 'AGC, Doppler', '4-band FPA'],
            ['Actuators', '8 power lines', '4xRW, 3xMTQ', '3 heaters, cooler', '\u2014', 'PA, antenna', 'Cooler, shutter'],
            ['Failure modes', '4', '5', '3', '3', '4', '4'],
            ['FDIR actions', 'Load shed', 'Auto-detumble', '\u2014', 'Watchdog reboot', 'PA shutdown', '\u2014'],
        ],
        col_widths=[16, 12, 20, 14, 16, 14, 14],
    )

    # ═══════════════════ SECTION 7 ═══════════════════
    pdf.section_header('7. PUS Service Architecture & Command Flow')

    pdf.sub_header('7.1 Implemented PUS Services')
    pdf.table(
        ['Service', 'Name', 'Subtypes', 'Description'],
        [
            ['S1', 'Request Verification', '1,2,3,4,7,8', 'Accept/reject/start/complete/fail reports for every TC'],
            ['S3', 'Housekeeping', '1,2,5,6,25,27,31', 'HK definition CRUD, periodic/one-shot, interval modification'],
            ['S5', 'Event Reporting', '1-4,5,6,7,8', 'Event generation (4 severities), type enable/disable'],
            ['S6', 'Memory Management', '2,5,9', 'Memory load, dump, check (simplified)'],
            ['S8', 'Function Management', '1', 'Subsystem command routing (~60 function IDs)'],
            ['S9', 'Time Management', '1,2', 'Set/request CUC time'],
            ['S11', 'Scheduling', '4,7,9,11,13,17', 'Time-tagged TC queue: insert, delete, enable, list'],
            ['S12', 'On-Board Monitoring', '1,2,6,7,9,10,12', 'Parameter limit definitions, per-tick check, transition reports'],
            ['S15', 'Onboard Storage', '1,2,9,11,13', '4 stores (HK/Event/Science/Alarm), dump, delete, status'],
            ['S17', 'Connection Test', '1,2', 'Echo request/response (link verification)'],
            ['S19', 'Event-Action', '1,2,4,5,8', 'Event->S8 function linking, auto-response triggers'],
            ['S20', 'Parameter Management', '1,2,3', 'Direct parameter get/set'],
        ],
        col_widths=[8, 22, 18, 52],
    )

    pdf.sub_header('7.2 S8 Function ID Routing')
    pdf.table(
        ['ID Range', 'Subsystem', 'Functions'],
        [
            ['0\u20139', 'AOCS', 'set_mode, desaturate, wheel enable/disable, ST power/select, MAG select, MTQ'],
            ['10\u201315', 'EPS', 'payload_mode, FPA cooler, TX enable, power line on/off, OC reset'],
            ['20\u201327', 'Payload', 'set_mode, set_scene, capture, download, delete, mark_bad_seg, band_config'],
            ['30\u201335', 'TCS', 'heater on/off, FPA cooler, setpoint, auto mode'],
            ['40\u201347', 'OBDH', 'OBC mode, mem_scrub, reboot, switch unit, bus select, boot_app, boot_inhibit'],
            ['50\u201358', 'TTC', 'XPDR switch, TM rate, PA on/off, TX power, deploy, beacon, cmd_channel'],
        ],
        col_widths=[12, 14, 74],
    )

    pdf.sub_header('7.3 Command Dispatch Flow')
    pdf.code_block('TC Dispatch Pipeline (engine._dispatch_tc)',
        'Raw TC bytes\n'
        '  |\n'
        '  +- decommutate_packet() -> PrimaryHeader + SecondaryHeader + data\n'
        '  |\n'
        '  +- _check_tc_acceptance(service, subtype, data)\n'
        '  |   +- Phase gating (bootloader restricts to S17, S9.1, S8 func 42-47)\n'
        '  |   +- Power state check (is target subsystem powered?)\n'
        '  |   +- Returns (accepted: bool, error_code: int)\n'
        '  |\n'
        '  +- if rejected -> S1.2 Acceptance Failure TM -> enqueue\n'
        '  |\n'
        '  +- if accepted -> S1.1 Acceptance Success TM -> enqueue\n'
        '  |   |\n'
        '  |   +- S1.3 Execution Start TM -> enqueue\n'
        '  |   |\n'
        '  |   +- ServiceDispatcher.dispatch(service, subtype, data)\n'
        '  |   |   +- Routes to _handle_s3/s5/s6/s8/s9/s11/s12/s15/s17/s19/s20\n'
        '  |   |   +- S8 -> routes func_id to subsystem.handle_command()\n'
        '  |   |   +- Returns list[bytes] (response TM packets)\n'
        '  |   |\n'
        '  |   +- if success -> S1.7 Execution Complete TM -> enqueue\n'
        '  |   +- if failure -> S1.8 Execution Failure TM -> enqueue\n'
        '  |\n'
        '  +- All TM -> tm_queue -> broadcast to MCS'
    )

    # ═══════════════════ SECTION 8 ═══════════════════
    pdf.section_header('8. Telemetry Processing Pipeline')

    pdf.sub_header('8.1 TM Generation (Simulator Side)')
    pdf.code_block('Telemetry Generation Chain',
        'Engine tick loop (1 Hz)\n'
        '  |\n'
        '  +- Subsystem.tick() -> writes to shared_params[param_id]\n'
        '  |\n'
        '  +- _emit_hk_packets(dt)\n'
        '  |   +- For each enabled HK SID with elapsed interval:\n'
        '  |   |   TMBuilder.build_hk_packet(sid, params, hk_structure)\n'
        '  |   |   -> S3.25 TM packet (SID + packed parameter values)\n'
        '  |   +- Enqueue to tm_queue\n'
        '  |\n'
        '  +- _check_subsystem_events() -> edge detection\n'
        '  |   +- _emit_event() -> TMBuilder.build_event_packet()\n'
        '  |       -> S5.{1-4} TM packet (event_id, severity, CUC, aux_text)\n'
        '  |       +- trigger_event_action() -> may execute S8 auto-response\n'
        '  |\n'
        '  +- _tick_s12_monitoring() -> ServiceDispatcher.check_monitoring()\n'
        '  |   +- Out-of-limit? -> S12.9 Transition Report TM\n'
        '  |\n'
        '  +- _enqueue_tm(pkt)\n'
        '      +- if downlink_active: tm_queue.put(pkt)\n'
        '      +- always: TMStorage.store_packet(service, pkt)'
    )

    pdf.sub_header('8.2 TM Processing (MCS Side)')
    pdf.code_block('Telemetry Reception & Display',
        'TCP :8002 --read_framed_packet()--> MCSServer._tm_receive_loop()\n'
        '  |\n'
        '  +- decommutate_packet() -> DecommutatedPacket\n'
        '  |\n'
        '  +- _process_tm(pkt)\n'
        '  |   +- S1: Update _verification_log (accepted/rejected/completed/failed)\n'
        '  |   +- S3.25: TMProcessor._process_hk(data)\n'
        '  |   |   +- Extract SID -> lookup hk_structure\n'
        '  |   |   +- Decommutate parameters -> _params[id], _history[id]\n'
        '  |   |   +- Limit check -> alarms if out-of-range\n'
        '  |   +- S5: Extract event -> alarm_journal (if severity >= 3)\n'
        '  |   +- S12.9: Monitoring violation -> alarm_journal\n'
        '  |   +- S15: Playback data -> _tm_dump_data\n'
        '  |\n'
        '  +- TMArchive.store_parameters(params) -- every 10 seconds\n'
        '  |\n'
        '  +- WebSocket broadcast -> all connected clients\n'
        '      +- {type: "tm", packet: {...}}\n'
        '      +- {type: "alarm", alarm: {...}} (if applicable)'
    )

    # ═══════════════════ SECTION 9 ═══════════════════
    pdf.section_header('9. State Machine Diagrams')

    pdf.sub_header('9.1 Spacecraft Phase State Machine')
    pdf.code_block('Spacecraft Phase Transitions (engine._tick_spacecraft_phase)',
        '+-------------------+\n'
        '| 0: PRE_SEPARATION | -- Everything OFF, no subsystem ticks\n'
        '+--------+----------+\n'
        '         | [instructor: start_separation]\n'
        '         v\n'
        '+---------------------+\n'
        '| 1: SEPARATION_TIMER | -- 30-min countdown, OBC+RX ON, limited subsystems\n'
        '+--------+------------+\n'
        '         | [timer expires]\n'
        '         v\n'
        '+---------------------+\n'
        '| 2: INITIAL_POWER_ON | -- OBC to bootloader (sw_image=0)\n'
        '+--------+------------+\n'
        '         | [immediate]\n'
        '         v\n'
        '+---------------------+\n'
        '| 3: BOOTLOADER_OPS   | -- Beacon only, SID 10/11, restricted TCs\n'
        '+--------+------------+    (S17, S9.1, S8 func 42-47 only)\n'
        '         | [boot_app complete OR instructor]\n'
        '         v\n'
        '+---------------------+\n'
        '| 4: LEOP             | -- EPS, TTC, OBDH active; AOCS/TCS/Payload gated\n'
        '+--------+------------+\n'
        '         | [instructor command]\n'
        '         v\n'
        '+---------------------+\n'
        '| 5: COMMISSIONING    | -- All subsystems active, checkout mode\n'
        '+--------+------------+\n'
        '         | [instructor command]\n'
        '         v\n'
        '+---------------------+\n'
        '| 6: NOMINAL          | -- Full operations\n'
        '+---------------------+'
    )

    pdf.sub_header('9.2 AOCS Mode State Machine')
    pdf.code_block('AOCS Autonomous Mode Transitions',
        '                         +-------------------+\n'
        '                    +-->| 1: SAFE_BOOT (30s)|---+\n'
        '                    |    +-------------------+   | [time >= 30s]\n'
        '                    |                            v\n'
        '              [power on]               +-----------------+\n'
        '         +----------+                  |  2: DETUMBLE     |\n'
        '         | 0: OFF   |                  |  MTQ B-dot ctrl  |\n'
        '         +----------+                  +--------+--------+\n'
        '                                                | [rate < 0.5 deg/s for 30s]\n'
        '                                                v\n'
        '                                       +-----------------+\n'
        '                    +---------------->|  3: COARSE_SUN   |<----------+\n'
        '                    |                  |  CSS pointing     |           |\n'
        '                    |                  +--------+---------+           |\n'
        '                    |                           | [CSS valid +        |\n'
        '                    |                           |  att_err < 10 deg   |\n'
        '                    |                           |  for 60s + ST valid]|\n'
        '                    |                           v                     |\n'
        '         [eclipse exit           +-------------------+     [eclipse exit\n'
        '          + ST invalid]          |  4: NOMINAL        |      + ST invalid]\n'
        '                    |            +--+----------+------+           |\n'
        '                    |               |          |                  |\n'
        '                    |    [eclipse + |          | [cmd]            |\n'
        '                    |     ST blind] |          v                  |\n'
        '                    |               |   +------------+           |\n'
        '                    |               +-->| 8: ECLIPSE |-----------+\n'
        '                    |                   | CSS+MAG ctrl|\n'
        '                    +-------------------+            |\n'
        '\n'
        ' Separate: 5: FINE_POINT (commanded), 6: SLEW (commanded)\n'
        ' Emergency: rate > 2.0 deg/s from any mode -> DETUMBLE'
    )

    pdf.sub_header('9.3 Procedure Runner State Machine')
    pdf.code_block('ProcedureRunner States',
        '+------+  load()   +--------+  start()  +---------+\n'
        '| IDLE |---------->| LOADED |---------->| RUNNING |\n'
        '+------+           +--------+           +--+---+--+\n'
        '                                     pause |   | complete\n'
        '                                           v   |\n'
        '                                    +--------+  |    +-----------+\n'
        '                                    | PAUSED |  +--->| COMPLETED |\n'
        '                                    +---+----+       +-----------+\n'
        '                                 resume |   abort\n'
        '                                        |     |     +---------+\n'
        '                                 RUNNING<+     +--->| ABORTED |\n'
        '                                                     +---------+\n'
        '\n'
        '    Step types: wait_s, wait_for (condition polling), command (S{n}.{m})\n'
        '    Step-by-step mode: pauses after each step, requires step_advance()'
    )

    # ═══════════════════ SECTION 10 ═══════════════════
    pdf.section_header('10. Sequence Diagrams')

    pdf.sub_header('10.1 Telecommand Execution Sequence')
    pdf.code_block('TC Send -> Verify -> Execute -> Report',
        'Operator       MCS Server       TC Socket       Simulator Engine     Subsystem\n'
        '   |               |                |                |                  |\n'
        '   |-POST /api/    |                |                |                  |\n'
        '   |  pus-command->|                |                |                  |\n'
        '   |               |-build_command()|                |                  |\n'
        '   |               |-tc_send_lock-->|                |                  |\n'
        '   |               |  frame+write   |--framed TC--->|                  |\n'
        '   |               |               |                |-decommutate()    |\n'
        '   |               |               |                |-check_accept()   |\n'
        '   |               |               |                |-S1.1 TM-------->|(enqueue)\n'
        '   |               |               |<--S1.1 TM-----|                  |\n'
        '   |               |<-decommutate--|                |-dispatch()       |\n'
        '   |               |-verify_log    |                |----cmd--------->|\n'
        '   |               |               |                |                  |-execute\n'
        '   |               |               |                |<--result---------|  \n'
        '   |               |               |                |-S1.7 TM-------->|(enqueue)\n'
        '   |               |               |<--S1.7 TM-----|                  |\n'
        '   |<-WS: verify--|<-decommutate--|                |                  |\n'
        '   |   complete    |               |                |                  |'
    )

    pdf.sub_header('10.2 HK Telemetry Cycle')
    pdf.code_block('Periodic Housekeeping (1 Hz tick)',
        'Engine          Subsystem      TMBuilder       TM Queue         MCS Server\n'
        '  |                |               |               |                |\n'
        '  |-tick(dt)------>|               |               |                |\n'
        '  |               |-update params  |               |                |\n'
        '  |<-params[id]---|               |               |                |\n'
        '  |                                |               |                |\n'
        '  |-(HK interval elapsed)-------->|               |                |\n'
        '  |               build_hk_packet() |               |                |\n'
        '  |<------S3.25 TM bytes-----------|               |                |\n'
        '  |                                                 |                |\n'
        '  |-enqueue_tm()-------------------------------------->|                |\n'
        '  |                                                 |-TCP broadcast->|\n'
        '  |                                                 |                |-process\n'
        '  |                                                 |                |-charts\n'
        '  |                                                 |                |-limits\n'
        '  |                                                 |                |-WS push'
    )

    # ═══════════════════ SECTION 11 ═══════════════════
    pdf.section_header('11. Network Topology & Deployment')

    pdf.sub_header('11.1 Single-Machine Deployment')
    pdf.code_block('Default localhost deployment (start.sh)',
        '+----------------------------------------------------------------+\n'
        '|                        localhost                                |\n'
        '|                                                                |\n'
        '|  +------------------+  TC :8001  +------------------+          |\n'
        '|  |  Simulator       |<-----------|  MCS Server      |          |\n'
        '|  |  HTTP :8080      |----------->|  HTTP :9090      |          |\n'
        '|  |  (Instructor UI) |  TM :8002  |  (Operator UI)   |          |\n'
        '|  +------------------+            +--------+---------+          |\n'
        '|                                           | HTTP                |\n'
        '|                                  +--------v---------+          |\n'
        '|                                  |  Planner Server  |          |\n'
        '|                                  |  HTTP :9091      |          |\n'
        '|                                  |  (Planning UI)   |          |\n'
        '|                                  +------------------+          |\n'
        '|                                                                |\n'
        '|  Browser clients: http://localhost:9090  (MCS)                 |\n'
        '|                   http://localhost:9091  (Planner)             |\n'
        '|                   http://localhost:8080  (Instructor)          |\n'
        '+----------------------------------------------------------------+'
    )

    pdf.sub_header('11.2 Distributed Deployment (with Gateway)')
    pdf.code_block('Multi-site deployment via smo-env.conf',
        '+-------------------+          +-------------------+\n'
        '|  Simulator Host   |          |  Gateway Host     |\n'
        '|  TC :8001         |<--TCP----|  :10025           |\n'
        '|  TM :8002         |---TCP--->|  (relay)          |\n'
        '|  Instr :8080      |          +-----+------+------+\n'
        '+-------------------+                |      |\n'
        '                              +------+      +------+\n'
        '                              |                     |\n'
        '                     +--------v-------+   +--------v-------+\n'
        '                     |  MCS Site A    |   |  MCS Site B    |\n'
        '                     |  :9090         |   |  :9090         |\n'
        '                     |  30 operators  |   |  10 operators  |\n'
        '                     +----------------+   +----------------+\n'
        '\n'
        'Capacity:  1 sim operator, 30 MCS TM viewers, 15 commanders, 10 planners\n'
        'Protocol:  TCP with 2-byte length-prefix framing (ECSS PUS-C packets)'
    )

    pdf.sub_header('11.3 Protocol Stack')
    pdf.table(
        ['Layer', 'Protocol', 'Used For'],
        [
            ['Application', 'ECSS PUS-C (12 services)', 'All TM/TC content'],
            ['Presentation', 'CCSDS Space Packet', 'Packet headers (APID, seq, CRC-16)'],
            ['Session', 'Length-prefix framing (2B BE)', 'TCP stream demultiplexing'],
            ['Transport', 'TCP', 'TM/TC sockets (reliable delivery)'],
            ['Web', 'HTTP/1.1 + WebSocket', 'APIs, real-time UI updates'],
        ],
        col_widths=[18, 30, 52],
    )

    # ═══════════════════ SECTION 12 ═══════════════════
    pdf.section_header('12. MCS User Interface Architecture')

    pdf.sub_header('12.1 Tab Structure')
    pdf.table(
        ['#', 'Tab', 'Content', 'Charts'],
        [
            ['1', 'Overview', 'SVG world map, subsystem summary grid, orbit bar, position info', '\u2014'],
            ['2', 'EPS', 'Battery SoC, power gen/consumed, bus voltage, TM dump playback', '4'],
            ['3', 'TCS', 'Temperature sensors, heater power, heater status', '2'],
            ['4', 'AOCS', 'Body rates, attitude error, RW cards, mini-map', '2'],
            ['5', 'TTC', 'RSSI, BER, ground station panel, contact timeline', '2'],
            ['6', 'Payload', 'FPA temperature, imaging status', '1'],
            ['7', 'OBDH', 'CPU load, memory/storage utilization', '1'],
            ['8', 'Commanding', 'PUS command builder, TC catalog, quick commands, verification log', '\u2014'],
            ['9', 'PUS Services', 'S3/S5/S6/S8/S12/S15/S19/S20 dedicated panels', '\u2014'],
            ['10', 'Procedures', 'Procedure browser, builder, execution controls, step results', '\u2014'],
            ['11', 'Manual', 'Split-pane: TOC sidebar + markdown content viewer', '\u2014'],
        ],
        col_widths=[5, 14, 68, 8],
    )

    pdf.sub_header('12.2 Bottom Panel Sub-Tabs')
    pdf.table(
        ['Sub-Tab', 'Content'],
        [
            ['Events', 'S5 event log with severity badges, subsystem filtering'],
            ['TM', 'Raw TM packet display with service/subtype breakdown'],
            ['CMD History', 'Verification log with S1 status badges (green/red/blue)'],
            ['Handover', 'Shift handover notes with timestamps'],
            ['GO-NOGO', 'Poll status, per-position response display'],
        ],
        col_widths=[20, 80],
    )

    pdf.sub_header('12.3 Operator Positions (Role-Based Access)')
    pdf.table(
        ['Position', 'Allowed Services', 'Subsystem Focus'],
        [
            ['flight_director', 'All services, all func IDs', 'Full access + GO/NO-GO initiation'],
            ['eps_tcs', 'S3, S5, S8 (func 10-15, 30-35), S12, S20', 'EPS + TCS'],
            ['aocs', 'S3, S5, S8 (func 0-9), S12, S20', 'AOCS flight dynamics'],
            ['ttc', 'S3, S5, S8 (func 50-58), S12, S15, S20', 'TT&C link management'],
            ['payload_ops', 'S3, S5, S8 (func 20-27), S12, S20', 'Payload operations'],
            ['fdir_systems', 'S3, S5, S8, S12, S19, S20', 'FDIR, OBDH, systems'],
        ],
        col_widths=[20, 40, 40],
    )

    # ═══════════════════ SECTION 13 ═══════════════════
    pdf.section_header('13. Mission Planner Architecture')

    pdf.sub_header('13.1 API Endpoints')
    pdf.table(
        ['Method', 'Endpoint', 'Purpose'],
        [
            ['GET', '/api/contacts', '24-hour contact window predictions'],
            ['GET', '/api/ground-track', 'Ground track (configurable duration/offset)'],
            ['GET', '/api/spacecraft-state', 'Real-time SC position, heading, eclipse'],
            ['GET', '/api/ground-stations', 'Ground station coordinates'],
            ['GET/POST', '/api/schedule', 'Activity CRUD with conflict detection'],
            ['POST', '/api/schedule/pass-activity', 'Schedule activity relative to pass AOS'],
            ['GET', '/api/budget/power', '24h SoC prediction at pass boundaries'],
            ['GET', '/api/budget/data', 'Downlink capacity per pass (elevation-dependent)'],
            ['GET', '/api/imaging/targets', '7 ocean current monitoring targets'],
            ['GET', '/api/imaging/opportunities', '24h imaging windows (swath intersection)'],
            ['POST', '/api/imaging/schedule', 'Create imaging activity with capture sequence'],
        ],
        col_widths=[12, 30, 58],
    )

    pdf.sub_header('13.2 Planning Computation Pipeline')
    pdf.code_block('Periodic Recompute Loop (every 10 min)',
        'OrbitPropagator.reset(now)\n'
        '  |\n'
        '  +- predict_ground_track(3h, 30s step) -> 360 points\n'
        '  |   +- {utc, lat, lon, alt_km, in_eclipse, solar_beta_deg}\n'
        '  |\n'
        '  +- contact_windows(24h, 10s step) -> per-station AOS/LOS\n'
        '  |\n'
        '  +- Cache results for API responses\n'
        '\n'
        'On-demand:\n'
        '  +- BudgetTracker.compute_power_budget()\n'
        '  |   +- SoC evolution: inter-pass drain + in-pass charge + activity consumption\n'
        '  |\n'
        '  +- BudgetTracker.compute_data_budget()\n'
        '  |   +- Per-pass downlink: elevation-dependent throughput x duration\n'
        '  |\n'
        '  +- ImagingPlanner.compute_opportunities(track, 24h)\n'
        '      +- For each track point: check target within swath (118 km)'
    )

    # ═══════════════════ SECTION 14 ═══════════════════
    pdf.section_header('14. Configuration & Data Model')

    pdf.sub_header('14.1 Configuration File Structure')
    pdf.code_block('configs/eosat1/ directory',
        'configs/eosat1/\n'
        '\u251c\u2500\u2500 mission.yaml                 Mission identity, PUS version, APID\n'
        '\u251c\u2500\u2500 orbit.yaml                   TLE, altitude, inclination, ground stations\n'
        '\u251c\u2500\u2500 subsystems/\n'
        '\u2502   \u251c\u2500\u2500 eps.yaml                 Solar arrays, battery, power lines\n'
        '\u2502   \u251c\u2500\u2500 aocs.yaml                RW config, modes, attitude deadband\n'
        '\u2502   \u251c\u2500\u2500 tcs.yaml                 Thermal zones, heater circuits, cooler\n'
        '\u2502   \u251c\u2500\u2500 obdh.yaml                OBC config, watchdog, memory\n'
        '\u2502   \u251c\u2500\u2500 ttc.yaml                 Link budget, frequencies, data rates\n'
        '\u2502   \u251c\u2500\u2500 payload.yaml             Multispectral bands, storage, compression\n'
        '\u2502   \u251c\u2500\u2500 fdir.yaml                Fault rules: parameter, condition, action\n'
        '\u2502   \u2514\u2500\u2500 memory_map.yaml          SRAM/EEPROM/Flash regions\n'
        '\u251c\u2500\u2500 telemetry/\n'
        '\u2502   \u251c\u2500\u2500 parameters.yaml          ~165 parameter definitions (id, name, units)\n'
        '\u2502   \u2514\u2500\u2500 hk_structures.yaml       HK packet composition (SID -> param list)\n'
        '\u251c\u2500\u2500 commands/\n'
        '\u2502   \u2514\u2500\u2500 tc_catalog.yaml          ~50 TC definitions (service, subtype, fields)\n'
        '\u251c\u2500\u2500 events/\n'
        '\u2502   \u2514\u2500\u2500 event_catalog.yaml       Event definitions with severity\n'
        '\u251c\u2500\u2500 mcs/\n'
        '\u2502   \u251c\u2500\u2500 displays.yaml            Operator display widget layouts\n'
        '\u2502   \u251c\u2500\u2500 limits.yaml              Parameter red/yellow thresholds\n'
        '\u2502   \u251c\u2500\u2500 positions.yaml           6 operator positions with RBAC rules\n'
        '\u2502   \u2514\u2500\u2500 pus_services.yaml        PUS service configuration\n'
        '\u251c\u2500\u2500 planning/\n'
        '\u2502   \u251c\u2500\u2500 activity_types.yaml      Activity definitions (power, data, constraints)\n'
        '\u2502   \u2514\u2500\u2500 imaging_targets.yaml     7 ocean monitoring target regions\n'
        '\u251c\u2500\u2500 procedures/                  64 procedures (YAML, 5 categories)\n'
        '\u2502   \u251c\u2500\u2500 procedure_index.yaml     Master index\n'
        '\u2502   \u251c\u2500\u2500 leop/ (7)\n'
        '\u2502   \u251c\u2500\u2500 commissioning/ (13)\n'
        '\u2502   \u251c\u2500\u2500 nominal/ (12)\n'
        '\u2502   \u251c\u2500\u2500 contingency/ (26)\n'
        '\u2502   \u2514\u2500\u2500 emergency/ (6)\n'
        '\u251c\u2500\u2500 manual/                      11 operator manual sections (Markdown)\n'
        '\u2514\u2500\u2500 scenarios/                   26 training scenarios (YAML)'
    )

    pdf.sub_header('14.2 Pydantic Config Schema Summary')
    pdf.body(
        'The smo_common.config.schemas module defines 29 Pydantic BaseModel classes for YAML validation. Key schemas include:'
    )
    pdf.table(
        ['Schema', 'Key Fields', 'Used By'],
        [
            ['MissionConfig', 'name, apid, pus_version', 'All packages'],
            ['OrbitConfig', 'TLE lines, altitude, ground_stations[]', 'Simulator, Planner'],
            ['EPSConfig', 'arrays[], battery, power lines, param_ids', 'Simulator'],
            ['AOCSConfig', 'modes[], reaction_wheels, deadband', 'Simulator'],
            ['PositionConfig', 'allowed_services[], allowed_func_ids[], visible_tabs[]', 'MCS'],
            ['HKStructureDef', 'sid, parameters[{id, format, scale}]', 'Simulator, MCS'],
            ['ScenarioConfig', 'events[{time_offset, action, params}], expected_responses', 'Simulator'],
            ['ActivityTypeConfig', 'duration, power, data_volume, conflicts_with, pre_conditions', 'Planner'],
        ],
        col_widths=[22, 48, 20],
    )

    # ═══════════════════ SECTION 15 ═══════════════════
    pdf.section_header('15. Concurrency & Thread Safety')

    pdf.sub_header('15.1 Threading Model')
    pdf.table(
        ['Component', 'Execution Context', 'Shared State Protection'],
        [
            ['SimulationEngine._run_loop', 'Dedicated daemon thread (1 Hz)', 'threading.Lock on params'],
            ['Instructor HTTP server', 'asyncio event loop (main thread)', 'Snapshot-on-read via _params_lock'],
            ['MCS Server', 'asyncio event loop', 'asyncio.Lock on _ws_clients, GO/NO-GO, TC send'],
            ['Gateway relay', 'asyncio event loop', 'asyncio.Lock on client lists'],
            ['ServiceDispatcher', 'Engine thread + async TC handlers', 'threading.Lock on all methods'],
            ['TCScheduler', 'Engine thread + HTTP queries', 'threading.Lock on all methods'],
            ['TC sequence counter', 'Multiple async command senders', 'itertools.count() (atomic)'],
        ],
        col_widths=[28, 32, 40],
    )

    pdf.sub_header('15.2 Concurrency Capacity')
    pdf.table(
        ['Role', 'Max Concurrent', 'Mechanism'],
        [
            ['Sim Operator (Instructor)', '1', 'HTTP :8080 + WebSocket'],
            ['MCS TM Viewers', '30', 'WebSocket broadcast (snapshot pattern)'],
            ['MCS Command Senders', '15', 'asyncio.Lock serialized TC, FIFO queue'],
            ['Planner Users', '10', 'HTTP stateless (per-request computation)'],
            ['Gateway Downstream', 'Multiple sites', 'Locked client list, broadcast pattern'],
        ],
        col_widths=[30, 18, 52],
    )

    # ═══════════════════ SECTION 16 ═══════════════════
    pdf.section_header('16. Test Architecture')

    pdf.sub_header('16.1 Test Coverage')
    pdf.table(
        ['Test Suite', 'Files', 'Focus Areas'],
        [
            ['test_common/', '5', 'Config validation/integrity, orbit propagation, ECSS protocol'],
            ['test_simulator/', '17', 'Subsystem models, PUS services, LEOP, link gating, S12/S19, TM storage'],
            ['test_mcs/', '10', 'Alarm journal, GO/NO-GO, procedures, position filtering, TM playback'],
            ['test_planner/', '7', 'Scheduling, budgets, ground track, imaging, pass scheduling'],
            ['test_gateway/', '2', 'Gateway relay, downstream manager'],
            ['test_integration/', '7', 'End-to-end flows, LEOP sequence, contingency recovery, scenarios'],
            ['Total', '48 files', '996 test cases, all passing'],
        ],
        col_widths=[20, 10, 70],
    )

    pdf.sub_header('16.2 Test Framework')
    pdf.bullet_list([
        'Framework: pytest >= 7.0 with pytest-asyncio >= 0.21',
        'Async mode: auto (seamless async test support)',
        'Mocking: unittest.mock (MagicMock, AsyncMock, patch)',
        'Coverage: All PUS services, all subsystem models, all state machines',
    ])

    # ═══════════════════ SECTION 17 ═══════════════════
    pdf.section_header('17. Class Index')

    pdf.table(
        ['Class', 'Package', 'File', 'Description'],
        [
            ['SubsystemModel', 'smo-common', 'models/subsystem.py', 'Abstract base class for subsystem simulation'],
            ['OrbitPropagator', 'smo-common', 'orbit/propagator.py', 'SGP4 orbit propagation with eclipse & contact'],
            ['OrbitState', 'smo-common', 'orbit/propagator.py', 'Dataclass: position, velocity, eclipse, GS geometry'],
            ['GroundStation', 'smo-common', 'orbit/propagator.py', 'Dataclass: station coordinates and min elevation'],
            ['PrimaryHeader', 'smo-common', 'protocol/ecss_packet.py', 'CCSDS primary header (6 bytes)'],
            ['SecondaryHeader', 'smo-common', 'protocol/ecss_packet.py', 'ECSS PUS secondary header'],
            ['TMBuilder', 'smo-common', 'telemetry/tm_builder.py', 'Assembles HK, event, verification TM packets'],
            ['ParameterRegistry', 'smo-common', 'telemetry/parameters.py', 'Parameter name/ID lookup registry'],
            ['SimulationEngine', 'smo-simulator', 'engine.py', 'Core tick loop, phase SM, subsystem orchestration'],
            ['ServiceDispatcher', 'smo-simulator', 'service_dispatch.py', 'PUS S1-S20 command routing & execution'],
            ['TCScheduler', 'smo-simulator', 'tc_scheduler.py', 'S11 time-tagged command queue'],
            ['OnboardTMStorage', 'smo-simulator', 'tm_storage.py', 'S15 onboard TM buffering (4 stores)'],
            ['FailureManager', 'smo-simulator', 'failure_manager.py', 'Step/gradual/intermittent failure injection'],
            ['ScenarioEngine', 'smo-simulator', 'scenario_engine.py', 'YAML scenario execution & scoring'],
            ['BreakpointManager', 'smo-simulator', 'breakpoints.py', 'State save/restore for debugging'],
            ['EPSBasicModel', 'smo-simulator', 'models/eps_basic.py', 'Solar, battery, power lines, OC protection'],
            ['AOCSBasicModel', 'smo-simulator', 'models/aocs_basic.py', 'Attitude, 4 RW, dual ST/MAG, 6 CSS, MTQ'],
            ['TCSBasicModel', 'smo-simulator', 'models/tcs_basic.py', 'Thermal zones, 3 heaters, FPA cooler'],
            ['OBDHBasicModel', 'smo-simulator', 'models/obdh_basic.py', 'Dual OBC, dual CAN bus, bootloader, watchdog'],
            ['TTCBasicModel', 'smo-simulator', 'models/ttc_basic.py', 'Link budget, PA, BER, lock acquisition'],
            ['PayloadBasicModel', 'smo-simulator', 'models/payload_basic.py', '4-band multispectral, FPA, image catalog'],
            ['MCSServer', 'smo-mcs', 'server.py', 'HTTP/WS server, 40+ endpoints, RBAC, alarms'],
            ['TCManager', 'smo-mcs', 'tc_manager.py', 'PUS TC packet builder & verification tracker'],
            ['TMProcessor', 'smo-mcs', 'tm_processor.py', 'HK decommutation, limit checks, history'],
            ['TMArchive', 'smo-mcs', 'tm_archive.py', 'SQLite persistent TM storage (7-day retention)'],
            ['ProcedureRunner', 'smo-mcs', 'procedure_runner.py', 'Step-by-step procedure execution engine'],
            ['PlannerServer', 'smo-planner', 'server.py', 'HTTP API, orbit recompute, 23 endpoints'],
            ['ActivityScheduler', 'smo-planner', 'activity_scheduler.py', 'Activity CRUD, conflict detection, pass-based'],
            ['BudgetTracker', 'smo-planner', 'budget_tracker.py', 'Power SoC & data volume budget computation'],
            ['ImagingPlanner', 'smo-planner', 'imaging_planner.py', 'Target management, swath opportunity detection'],
            ['Gateway', 'smo-gateway', 'gateway.py', 'Bidirectional TM/TC relay'],
            ['DownstreamManager', 'smo-gateway', 'downstream.py', 'Multi-client connection management'],
        ],
        col_widths=[20, 14, 28, 38],
    )

    # ═══════════════════ FOOTER ═══════════════════
    pdf.ln(10)
    pdf.set_fill_color(245, 245, 245)
    y0 = pdf.get_y()
    if y0 > 240:
        pdf.add_page()
    pdf.set_font('sans', '', 8)
    pdf.set_text_color(*GRAY)
    bw2 = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.set_x(pdf.l_margin)
    pdf.cell(bw2, 20, '', fill=True, border=0)
    pdf.set_xy(pdf.l_margin, pdf.get_y() - 18)
    pdf.cell(bw2, 5, 'EOSAT-1 Space Mission Operations Suite \u2014 Code Design Document', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.cell(bw2, 5, 'SMO-DD-001 Rev 1.0 \u2014 2026-04-01', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.cell(bw2, 5, 'Generated by Claude Code (AI) \u2014 5 packages, 32 classes, 12 PUS services, 996 tests', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(3)
    # AIG banner
    bw3 = 60
    bx3 = (pdf.w - bw3) / 2
    pdf.set_fill_color(17, 17, 17)
    pdf.set_text_color(*WHITE)
    pdf.set_font('sans', 'B', 9)
    pdf.set_x(bx3)
    pdf.cell(bw3, 8, 'AIG \u2014 AI Generated', fill=True, align='C')

    # ── Output ──
    out = '/Users/FNewland/SpaceMissionSimulation/docs/EOSAT1_SMO_Code_Design_Document.pdf'
    pdf.output(out)
    print(f'PDF written to {out}  ({pdf.pages_count} pages)')


if __name__ == '__main__':
    build_pdf()
