// ============================================================
// NSFNET IoT Dashboard — Google Apps Script 後端
// 直接全選複製貼到 Google Apps Script 編輯器即可
// ============================================================

const SHEET_ID = '1sv_yBP9L7b78qduXbA1ns47J0h_uUipQtvb6eji6W98';
const MAX_SNAPSHOT_ROWS = 500;
const MAX_ARC_LOG_ROWS = 2000;

// ============================================================
// doPost：接收 simulator.py 推來的狀態
// ============================================================
function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);
    if (data.action === 'snapshot') {
      writeSnapshot(data);
      writeArcLog(data);
    }
    return ContentService
      .createTextOutput(JSON.stringify({ status: 'ok', round: data.round }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ status: 'error', message: err.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// ============================================================
// doGet：前端 fetch，回傳最新狀態 JSON
// ============================================================
function doGet(e) {
  return ContentService
    .createTextOutput(JSON.stringify(buildPayload()))
    .setMimeType(ContentService.MimeType.JSON);
}

// ============================================================
// writeSnapshot：寫入工作表1（每輪一筆快照）
// ============================================================
function writeSnapshot(data) {
  const ss = SpreadsheetApp.openById(SHEET_ID);
  const sheet = getOrCreateSheet(ss, '工作表1',
    ['Timestamp', 'Round', 'PPO_Throughput', 'ECMP_Throughput',
      'PPO_Efficiency', 'ECMP_Efficiency', 'Total_Available',
      'Active_Arcs', 'Degraded_Arcs', 'Failed_Arcs']);

  sheet.appendRow([
    data.timestamp,
    data.round,
    data.ppo_throughput,
    data.ecmp_throughput,
    data.ppo_efficiency,
    data.ecmp_efficiency,
    data.total_available,
    data.active_arcs,
    data.degraded_arcs,
    data.failed_arcs
  ]);

  const lastRow = sheet.getLastRow();
  if (lastRow > MAX_SNAPSHOT_ROWS + 1) {
    sheet.deleteRow(2);
  }
}

// ============================================================
// writeArcLog：寫入 arc_log（每輪寫全部 20 條 arc）
// ============================================================
function writeArcLog(data) {
  if (!data.arc_states || data.arc_states.length === 0) return;

  const ss = SpreadsheetApp.openById(SHEET_ID);
  const sheet = getOrCreateSheet(ss, 'arc_log',
    ['Timestamp', 'Round', 'arc_id', 'capacity', 'max_capacity', 'utilization', 'status']);

  const rows = data.arc_states.map(arc => [
    data.timestamp,
    data.round,
    arc.arc_id,
    arc.capacity,
    arc.max_capacity,
    arc.utilization,
    arc.status
  ]);
  sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, 7).setValues(rows);

  const lastRow = sheet.getLastRow();
  if (lastRow > MAX_ARC_LOG_ROWS + 1) {
    sheet.deleteRows(2, lastRow - MAX_ARC_LOG_ROWS - 1);
  }
}

// ============================================================
// buildPayload：組裝給前端的 JSON
// ============================================================
function buildPayload() {
  const ss = SpreadsheetApp.openById(SHEET_ID);

  // 快照歷史（最新 60 筆）
  const snapSheet = ss.getSheetByName('工作表1');
  const snapRows = snapSheet ? snapSheet.getDataRange().getValues() : [];
  const snapHistory = [];
  for (let i = snapRows.length - 1; i >= 1 && snapHistory.length < 60; i--) {
    const r = snapRows[i];
    snapHistory.unshift({
      timestamp: r[0],
      round: r[1],
      ppo_throughput: r[2],
      ecmp_throughput: r[3],
      ppo_efficiency: r[4],
      ecmp_efficiency: r[5],
      total_available: r[6],
      active_arcs: r[7],
      degraded_arcs: r[8],
      failed_arcs: r[9]
    });
  }
  const latest = snapHistory.length > 0 ? snapHistory[snapHistory.length - 1] : null;

  // 最新一輪 arc 狀態
  const arcSheet = ss.getSheetByName('arc_log');
  const arcRows = arcSheet ? arcSheet.getDataRange().getValues() : [];
  let latestRound = arcRows.length > 1 ? arcRows[arcRows.length - 1][1] : 0;

  const latestArcStates = {};
  for (let i = arcRows.length - 1; i >= 1; i--) {
    const r = arcRows[i];
    if (r[1] < latestRound) break;
    if (!latestArcStates[r[2]]) {
      latestArcStates[r[2]] = {
        arc_id: r[2],
        capacity: r[3],
        max_capacity: r[4],
        utilization: r[5],
        status: r[6]
      };
    }
  }

  return {
    updated_at: new Date().toISOString(),
    latest_snapshot: latest,
    history: snapHistory,
    arc_states: Object.values(latestArcStates)
  };
}

// ============================================================
// 工具：取得或建立工作表
// ============================================================
function getOrCreateSheet(ss, name, headers) {
  let sheet = ss.getSheetByName(name);
  if (!sheet) {
    sheet = ss.insertSheet(name);
    sheet.appendRow(headers);
  }
  return sheet;
}
