/**
 * MCS Display Panels — New enhanced displays for EOSAT-1
 *
 * Provides rendering and interaction for:
 * 1. Contact Schedule Panel
 * 2. Power Budget Monitor
 * 3. FDIR/Alarm Panel
 * 4. Procedure Status Panel
 * 5. System Overview Dashboard
 * 6. Telemetry Trending with Chart.js
 */

class DisplayPanels {
  constructor() {
    this.charts = new Map();
    this.updateIntervals = new Map();
  }

  async initContactSchedule(container) {
    if (!container) return;
    try {
      const resp = await fetch('/api/displays/contact-schedule');
      const data = await resp.json();
      this.renderContactSchedule(container, data);
      this.updateContactSchedule(container);
    } catch (e) {
      console.error('Contact schedule load failed:', e);
    }
  }

  renderContactSchedule(container, data) {
    const { next_passes, current_contact_status } = data;

    let html = '<div class="contact-panel">';

    // Current status
    const status = current_contact_status;
    html += '<div class="contact-status">';
    if (status.in_contact) {
      html += `<div class="status-badge in-contact">IN CONTACT: ${status.ground_station}</div>`;
      html += `<div class="contact-info">Time to LOS: ${Math.round(status.time_to_los)}s</div>`;
      html += `<div class="contact-info">Current Elevation: ${status.current_elevation}°</div>`;
    } else {
      html += `<div class="status-badge no-contact">Next: ${status.ground_station}</div>`;
      if (status.time_to_aos !== undefined) {
        html += `<div class="contact-info">Time to AOS: ${Math.round(status.time_to_aos)}s</div>`;
      }
    }
    html += '</div>';

    // Passes table
    html += '<table class="data-table passes-table">';
    html += '<thead><tr>';
    html += '<th>GS</th><th>AOS</th><th>LOS</th><th>Duration</th><th>Max El</th><th>Data (MB)</th>';
    html += '</tr></thead><tbody>';

    for (const pass of next_passes) {
      const aosDate = new Date(pass.aos_time * 1000).toLocaleTimeString();
      const losDate = new Date(pass.los_time * 1000).toLocaleTimeString();
      const color = pass.elevation_color;
      html += `<tr class="elevation-${color}">`;
      html += `<td>${pass.ground_station}</td>`;
      html += `<td>${aosDate}</td>`;
      html += `<td>${losDate}</td>`;
      html += `<td>${pass.duration_min.toFixed(1)}m</td>`;
      html += `<td><span class="elev-badge elevation-${color}">${pass.max_elevation}°</span></td>`;
      html += `<td>${pass.data_downlink_capacity.toFixed(1)}</td>`;
      html += '</tr>';
    }

    html += '</tbody></table></div>';
    container.innerHTML = html;
  }

  updateContactSchedule(container) {
    const interval = setInterval(() => {
      fetch('/api/displays/contact-schedule')
        .then(r => r.json())
        .then(data => this.renderContactSchedule(container, data))
        .catch(e => console.error('Contact update failed:', e));
    }, 5000);
    this.updateIntervals.set('contact-schedule', interval);
  }

  async initPowerBudget(container) {
    if (!container) return;
    try {
      const resp = await fetch('/api/displays/power-budget');
      const data = await resp.json();
      this.renderPowerBudget(container, data);
      this.updatePowerBudget(container);
    } catch (e) {
      console.error('Power budget load failed:', e);
    }
  }

  renderPowerBudget(container, data) {
    let html = '<div class="power-panel">';

    // Power metrics
    html += '<div class="power-metrics">';
    html += `<div class="metric">
      <span class="label">Power Gen:</span>
      <span class="value">${data.power_gen_w.toFixed(1)} W</span>
    </div>`;
    html += `<div class="metric">
      <span class="label">Power Con:</span>
      <span class="value">${data.power_cons_w.toFixed(1)} W</span>
    </div>`;
    const marginClass = data.power_margin_w > 0 ? 'positive' : 'negative';
    html += `<div class="metric">
      <span class="label">Margin:</span>
      <span class="value ${marginClass}">${data.power_margin_w.toFixed(1)} W</span>
    </div>`;
    html += '</div>';

    // Battery status
    html += '<div class="battery-status">';
    html += `<div class="soc-bar">
      <div class="soc-fill" style="width: ${data.battery_soc_percent}%"></div>
    </div>`;
    html += `<div class="soc-label">SoC: ${data.battery_soc_percent.toFixed(1)}% (${data.soc_trend})</div>`;
    html += `<div class="battery-temp">Battery Temp: ${data.battery_temp_c.toFixed(1)}°C</div>`;
    html += '</div>';

    // Load shedding
    const shedClass = data.load_shedding_stage > 0 ? 'active' : 'nominal';
    html += `<div class="load-shedding ${shedClass}">
      Load Shedding: ${data.load_shedding_label}
    </div>`;

    // Eclipse status
    if (data.eclipse_active) {
      html += '<div class="eclipse-badge">IN ECLIPSE</div>';
    } else if (data.time_to_eclipse_entry_s !== null) {
      html += `<div class="eclipse-info">Time to eclipse: ${Math.round(data.time_to_eclipse_entry_s / 60)}m</div>`;
    }

    // Subsystem power breakdown (simple bar chart)
    html += '<div class="subsystem-power">';
    html += '<h4>Subsystem Power</h4>';
    for (const [subsys, power] of Object.entries(data.per_subsystem_power)) {
      const pct = (power / data.total_subsystem_power) * 100;
      html += `<div class="power-bar">
        <span class="label">${subsys.toUpperCase()}</span>
        <div class="bar"><div class="fill" style="width: ${pct}%"></div></div>
        <span class="value">${power.toFixed(1)}W</span>
      </div>`;
    }
    html += '</div>';
    html += '</div>';

    container.innerHTML = html;
  }

  updatePowerBudget(container) {
    const interval = setInterval(() => {
      fetch('/api/displays/power-budget')
        .then(r => r.json())
        .then(data => this.renderPowerBudget(container, data))
        .catch(e => console.error('Power update failed:', e));
    }, 3000);
    this.updateIntervals.set('power-budget', interval);
  }

  async initFDIRAlarms(container) {
    if (!container) return;
    try {
      const resp = await fetch('/api/displays/fdir-alarms');
      const data = await resp.json();
      this.renderFDIRAlarms(container, data);
      this.updateFDIRAlarms(container);
    } catch (e) {
      console.error('FDIR alarms load failed:', e);
    }
  }

  renderFDIRAlarms(container, data) {
    let html = '<div class="fdir-panel">';

    // FDIR Level
    const levelColor = data.fdir_level_color;
    html += `<div class="fdir-level level-${levelColor}">
      FDIR Level: ${data.fdir_level.toUpperCase()}
    </div>`;

    // Alarm counts
    const counts = data.alarm_count_by_severity;
    html += '<div class="alarm-counts">';
    html += `<span class="critical">CRITICAL: ${counts.CRITICAL}</span>`;
    html += `<span class="high">HIGH: ${counts.HIGH}</span>`;
    html += `<span class="medium">MEDIUM: ${counts.MEDIUM}</span>`;
    html += `<span class="low">LOW: ${counts.LOW}</span>`;
    html += '</div>';

    // Active alarms table
    html += '<h4>Active Alarms</h4>';
    if (data.active_alarms.length === 0) {
      html += '<p class="no-alarms">No active alarms</p>';
    } else {
      html += '<table class="data-table alarms-table">';
      html += '<thead><tr>';
      html += '<th>ID</th><th>Severity</th><th>Subsystem</th><th>Parameter</th><th>Value</th><th></th>';
      html += '</tr></thead><tbody>';

      for (const alarm of data.active_alarms) {
        const sevClass = alarm.severity.toLowerCase();
        html += `<tr class="alarm-${sevClass}">`;
        html += `<td>${alarm.id}</td>`;
        html += `<td>${alarm.severity}</td>`;
        html += `<td>${alarm.subsystem}</td>`;
        html += `<td>${alarm.parameter}</td>`;
        html += `<td>${alarm.value}</td>`;
        html += `<td><button class="ack-btn" onclick="displayPanels.ackAlarm(${alarm.id})">Ack</button></td>`;
        html += '</tr>';
      }

      html += '</tbody></table>';
    }

    // S12 monitoring
    html += '<h4>S12 Monitoring</h4>';
    html += `<div class="s12-status">
      Active Rules: ${data.s12_monitoring.active_rules} |
      Violations: ${data.s12_monitoring.violations}
    </div>`;

    // S19 event-action
    html += '<h4>S19 Event-Action</h4>';
    html += `<div class="s19-status">
      Active Rules: ${data.s19_event_action.active_rules} |
      Triggered: ${data.s19_event_action.triggered_count}
    </div>`;

    html += '</div>';
    container.innerHTML = html;
  }

  updateFDIRAlarms(container) {
    const interval = setInterval(() => {
      fetch('/api/displays/fdir-alarms')
        .then(r => r.json())
        .then(data => this.renderFDIRAlarms(container, data))
        .catch(e => console.error('FDIR update failed:', e));
    }, 2000);
    this.updateIntervals.set('fdir-alarms', interval);
  }

  async ackAlarm(alarmId) {
    try {
      await fetch(`/api/displays/alarms/${alarmId}/ack`, { method: 'POST' });
      console.log(`Alarm ${alarmId} acknowledged`);
    } catch (e) {
      console.error('Ack failed:', e);
    }
  }

  async initProcedureStatus(container) {
    if (!container) return;
    try {
      const resp = await fetch('/api/displays/procedure-status');
      const data = await resp.json();
      this.renderProcedureStatus(container, data);
      this.updateProcedureStatus(container);
    } catch (e) {
      console.error('Procedure status load failed:', e);
    }
  }

  renderProcedureStatus(container, data) {
    let html = '<div class="procedure-panel">';

    const proc = data.executing_procedure;
    if (proc) {
      html += `<div class="executing">
        <h4>${proc.name}</h4>
        <div class="progress-bar">
          <div class="progress-fill" style="width: ${proc.progress_percent}%"></div>
        </div>
        <div class="progress-label">
          Step ${proc.current_step} of ${proc.total_steps} (${proc.progress_percent}%)
        </div>
        <div class="state-badge state-${proc.state}">${proc.state}</div>
      </div>`;

      // Steps
      html += '<div class="steps">';
      for (const step of proc.steps) {
        let stepClass = 'pending';
        if (step.is_current) stepClass = 'current';
        if (step.is_completed) stepClass = 'completed';

        html += `<div class="step step-${stepClass}">
          <span class="step-num">${step.number}</span>
          <span class="step-name">${step.name}</span>
        </div>`;
      }
      html += '</div>';
    } else {
      html += '<p class="no-procedure">No procedure running</p>';
    }

    // Available procedures
    html += '<h4>Available Procedures</h4>';
    if (data.available_procedures.length === 0) {
      html += '<p>No procedures available</p>';
    } else {
      html += '<ul class="procedure-list">';
      for (const p of data.available_procedures.slice(0, 5)) {
        html += `<li>${p.name || p.id}</li>`;
      }
      html += '</ul>';
    }

    html += '</div>';
    container.innerHTML = html;
  }

  updateProcedureStatus(container) {
    const interval = setInterval(() => {
      fetch('/api/displays/procedure-status')
        .then(r => r.json())
        .then(data => this.renderProcedureStatus(container, data))
        .catch(e => console.error('Procedure update failed:', e));
    }, 3000);
    this.updateIntervals.set('procedure-status', interval);
  }

  async initSystemOverview(container) {
    if (!container) return;
    try {
      const resp = await fetch('/api/displays/system-overview');
      const data = await resp.json();
      this.renderSystemOverview(container, data);
      this.updateSystemOverview(container);
    } catch (e) {
      console.error('System overview load failed:', e);
    }
  }

  renderSystemOverview(container, data) {
    let html = '<div class="system-overview">';

    // Satellite mode
    const modeColor = data.satellite_mode_color;
    html += `<div class="sat-mode mode-${modeColor}">
      ${data.satellite_mode}
    </div>`;

    // Subsystem health grid
    html += '<div class="health-grid">';
    for (const subsys of data.subsystem_health) {
      const color = subsys.status;
      html += `<div class="health-box health-${color}" title="${subsys.description}">
        <div class="health-name">${subsys.name}</div>
        <div class="health-status">${color.toUpperCase()}</div>
      </div>`;
    }
    html += '</div>';

    // Key parameters
    html += '<div class="key-params">';
    html += '<h4>Key Parameters</h4>';
    for (const param of data.key_parameters) {
      const pColor = param.status;
      html += `<div class="param-row param-${pColor}">
        <span class="param-name">${param.name}</span>
        <span class="param-value">${param.value.toFixed(2)} ${param.units}</span>
      </div>`;
    }
    html += '</div>';

    // Contact & alarms
    html += '<div class="contact-alarms">';
    html += `<div class="item">
      <span>Active Contacts:</span>
      <span class="value">${data.active_contacts}</span>
    </div>`;
    if (data.next_contact_countdown_min !== null) {
      html += `<div class="item">
        <span>Next Contact:</span>
        <span class="value">${data.next_contact_countdown_min.toFixed(1)}m</span>
      </div>`;
    }
    const alarmTotal = data.active_alarms.total;
    const alarmClass = alarmTotal > 0 ? 'alarm-active' : 'nominal';
    html += `<div class="item alarm-item ${alarmClass}">
      <span>Active Alarms:</span>
      <span class="value">${alarmTotal}</span>
    </div>`;
    html += '</div>';

    html += '</div>';
    container.innerHTML = html;
  }

  updateSystemOverview(container) {
    const interval = setInterval(() => {
      fetch('/api/displays/system-overview')
        .then(r => r.json())
        .then(data => this.renderSystemOverview(container, data))
        .catch(e => console.error('Overview update failed:', e));
    }, 5000);
    this.updateIntervals.set('system-overview', interval);
  }

  createTrendingChart(canvasId, config) {
    if (!window.Chart) {
      console.warn('Chart.js not loaded');
      return null;
    }

    const ctx = document.getElementById(canvasId)?.getContext('2d');
    if (!ctx) return null;

    const chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: config.labels || [],
        datasets: config.datasets || [],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 0 },
        plugins: {
          legend: { display: true },
        },
        scales: {
          y: {
            min: config.yMin,
            max: config.yMax,
          },
        },
      },
    });

    this.charts.set(canvasId, chart);
    return chart;
  }

  updateChart(canvasId, newLabels, newDatasets) {
    const chart = this.charts.get(canvasId);
    if (chart) {
      chart.data.labels = newLabels;
      chart.data.datasets = newDatasets;
      chart.update();
    }
  }

  destroy() {
    // Clear all update intervals
    for (const interval of this.updateIntervals.values()) {
      clearInterval(interval);
    }
    this.updateIntervals.clear();

    // Destroy all charts
    for (const chart of this.charts.values()) {
      chart.destroy();
    }
    this.charts.clear();
  }
}

// Global instance
const displayPanels = new DisplayPanels();
