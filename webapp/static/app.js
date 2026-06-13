const form = document.querySelector("#deployForm");
const versionSelect = document.querySelector("#versionSelect");
const submitButton = document.querySelector("#submitButton");
const serviceStatus = document.querySelector("#serviceStatus");
const progressPanel = document.querySelector("#progressPanel");
const progressFill = document.querySelector("#progressFill");
const progressPercent = document.querySelector("#progressPercent");
const phaseText = document.querySelector("#phaseText");
const eventList = document.querySelector("#eventList");
const resultPanel = document.querySelector("#resultPanel");
const resultTitle = document.querySelector("#resultTitle");
const summaryBlock = document.querySelector("#summaryBlock");
const consoleLinks = document.querySelector("#consoleLinks");
const errorPanel = document.querySelector("#errorPanel");
const errorText = document.querySelector("#errorText");
const mcpToggle = document.querySelector("#mcpToggle");
const mcpClose = document.querySelector("#mcpClose");
const mcpPanel = document.querySelector("#mcpPanel");
const mcpUrl = document.querySelector("#mcpUrl");
const mcpCommand = document.querySelector("#mcpCommand");
const mcpConfig = document.querySelector("#mcpConfig");

let pollTimer = null;

function setStatus(text) {
  serviceStatus.textContent = text;
}

function resetPanels() {
  progressPanel.hidden = true;
  resultPanel.hidden = true;
  errorPanel.hidden = true;
  eventList.innerHTML = "";
  consoleLinks.innerHTML = "";
  summaryBlock.textContent = "";
  errorText.textContent = "";
  progressFill.style.width = "0%";
  progressPercent.textContent = "0%";
  phaseText.textContent = "Queued";
}

function renderEvents(events) {
  eventList.innerHTML = "";
  for (const event of events.slice().reverse()) {
    const li = document.createElement("li");
    const time = document.createElement("span");
    const message = document.createElement("span");
    time.className = "event-time";
    time.textContent = event.time;
    message.textContent = event.message;
    li.append(time, message);
    eventList.append(li);
  }
}

function renderConsoleLinks(result) {
  consoleLinks.innerHTML = "";
  const host = result?.esxi_host;
  const vms = result?.vms || [];
  if (result?.mode !== "deploy" || !host || vms.length === 0) {
    return;
  }

  const manageCard = document.createElement("article");
  manageCard.className = "console-card manage-card";

  const manageTitle = document.createElement("h3");
  manageTitle.textContent = "Manage Port Group in ESXi";

  const manageLink = document.createElement("a");
  manageLink.className = "manage-link";
  manageLink.href = `https://${host}/ui`;
  manageLink.target = "_blank";
  manageLink.rel = "noopener";
  manageLink.textContent = host;

  manageCard.append(manageTitle, manageLink);
  consoleLinks.append(manageCard);

  for (const vm of vms) {
    const card = document.createElement("article");
    card.className = "console-card";

    const title = document.createElement("h3");
    title.textContent = vm.name;
    card.append(title);

    const ports = [
      ["IOS console", vm.serial1],
      ["Aux/Linux shell", vm.serial2],
    ];

    if (Number.isInteger(vm.network_adapters_disconnected)) {
      const networkRow = document.createElement("div");
      networkRow.className = "console-row";

      const networkLabel = document.createElement("span");
      networkLabel.textContent = "Network adapters";

      const networkValue = document.createElement("strong");
      networkValue.className = "network-status";
      networkValue.textContent = `${vm.network_adapters_disconnected} disconnected`;

      networkRow.append(networkLabel, networkValue);
      card.append(networkRow);
    }

    for (const [label, port] of ports) {
      const row = document.createElement("div");
      row.className = "console-row";

      const labelNode = document.createElement("span");
      labelNode.textContent = label;

      const command = `telnet ${host} ${port}`;
      const commandGroup = document.createElement("div");
      commandGroup.className = "console-command";

      const link = document.createElement("a");
      link.href = `telnet://${host}:${port}`;
      link.textContent = command;
      link.rel = "noopener";

      const copyButton = document.createElement("button");
      copyButton.type = "button";
      copyButton.className = "copy-command";
      copyButton.title = `Copy ${command}`;
      copyButton.setAttribute("aria-label", `Copy ${command}`);
      copyButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="10" height="10" rx="2"></rect><path d="M5 15V7a2 2 0 0 1 2-2h8"></path></svg>';
      copyButton.addEventListener("click", () => copyCommand(command, copyButton));

      commandGroup.append(link, copyButton);
      row.append(labelNode, commandGroup);
      card.append(row);
    }

    consoleLinks.append(card);
  }
}

async function copyCommand(command, button) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(command);
    } else {
      const textArea = document.createElement("textarea");
      textArea.value = command;
      textArea.setAttribute("readonly", "");
      textArea.style.position = "fixed";
      textArea.style.left = "-9999px";
      document.body.append(textArea);
      textArea.select();
      document.execCommand("copy");
      textArea.remove();
    }
    const previousTitle = button.title;
    button.classList.add("copied");
    button.title = "Copied";
    setTimeout(() => {
      button.classList.remove("copied");
      button.title = previousTitle;
    }, 1200);
  } catch {
    button.title = "Copy failed";
  }
}

async function copyText(text, button) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const textArea = document.createElement("textarea");
      textArea.value = text;
      textArea.setAttribute("readonly", "");
      textArea.style.position = "fixed";
      textArea.style.left = "-9999px";
      document.body.append(textArea);
      textArea.select();
      document.execCommand("copy");
      textArea.remove();
    }
    const previousText = button.textContent;
    button.textContent = "Copied";
    setTimeout(() => {
      button.textContent = previousText;
    }, 1200);
  } catch {
    button.textContent = "Copy failed";
  }
}

function setupMcpDetails() {
  const origin = window.location.origin || "http://10.76.90.60";
  const url = `${origin}/mcp`;
  mcpUrl.textContent = url;
  mcpCommand.textContent = `codex mcp add cat9kv --url ${url}`;
  mcpConfig.textContent = `[mcp_servers.cat9kv]\nurl = "${url}"`;

  mcpToggle.addEventListener("click", () => {
    mcpPanel.hidden = !mcpPanel.hidden;
  });
  mcpClose.addEventListener("click", () => {
    mcpPanel.hidden = true;
  });
  for (const button of document.querySelectorAll(".copy-inline")) {
    button.addEventListener("click", () => {
      const target = document.querySelector(`#${button.dataset.copyTarget}`);
      copyText(target.textContent, button);
    });
  }
}

async function loadVersions() {
  const response = await fetch("/api/versions");
  if (!response.ok) {
    throw new Error("Unable to load Cat9kV versions");
  }
  const data = await response.json();
  versionSelect.innerHTML = "";
  for (const version of data.versions) {
    const option = document.createElement("option");
    option.value = version.name;
    option.textContent = `${version.name} (${version.token})`;
    versionSelect.append(option);
  }
}

async function pollJob(jobId) {
  const response = await fetch(`/api/jobs/${jobId}`);
  if (!response.ok) {
    throw new Error("Unable to read job status");
  }
  const job = await response.json();
  progressPanel.hidden = false;
  phaseText.textContent = job.phase;
  progressFill.style.width = `${job.progress}%`;
  progressPercent.textContent = `${job.progress}%`;
  renderEvents(job.events || []);

  if (job.status === "completed") {
    clearInterval(pollTimer);
    pollTimer = null;
    submitButton.disabled = false;
    setStatus("Complete");
    resultPanel.hidden = false;
    progressPanel.hidden = true;
    resultTitle.textContent = job.result?.mode === "dry_run" ? "Dry Run Summary" : "Deployment Summary";
    renderConsoleLinks(job.result);
    summaryBlock.textContent = job.result?.summary || "";
    return;
  }

  if (job.status === "error") {
    clearInterval(pollTimer);
    pollTimer = null;
    submitButton.disabled = false;
    setStatus("Error");
    errorPanel.hidden = false;
    errorText.textContent = job.error || "Workflow failed. Contact rkaithar@cisco.com for further help.";
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  resetPanels();
  submitButton.disabled = true;
  setStatus("Running");

  const data = new FormData(form);
  const payload = {
    esxi_host: data.get("esxi_host"),
    username: data.get("username"),
    password: data.get("password"),
    version: data.get("version"),
    vm_count: Number(data.get("vm_count")),
    mode: data.get("mode"),
  };

  try {
    const response = await fetch("/api/deploy", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || "Unable to start workflow");
    }
    const result = await response.json();
    progressPanel.hidden = false;
    pollTimer = setInterval(() => {
      pollJob(result.job_id).catch((error) => {
        clearInterval(pollTimer);
        pollTimer = null;
        submitButton.disabled = false;
        setStatus("Error");
        errorPanel.hidden = false;
        errorText.textContent = `${error.message}. Contact rkaithar@cisco.com for further help.`;
      });
    }, 1200);
    await pollJob(result.job_id);
  } catch (error) {
    submitButton.disabled = false;
    setStatus("Error");
    errorPanel.hidden = false;
    errorText.textContent = `${error.message}. Contact rkaithar@cisco.com for further help.`;
  }
});

loadVersions().then(() => {
  setStatus("Ready");
}).catch((error) => {
  setStatus("Error");
  errorPanel.hidden = false;
  errorText.textContent = `${error.message}. Contact rkaithar@cisco.com for further help.`;
});

setupMcpDetails();
