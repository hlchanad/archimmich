document.addEventListener("DOMContentLoaded", () => {
    // DOM Elements - Login
    const loginContainer = document.getElementById("login-container");
    const loginForm = document.getElementById("login-form");
    const serverUrlInput = document.getElementById("server-url");
    const apiKeyInput = document.getElementById("api-key");
    const rememberMeCheckbox = document.getElementById("remember-me");
    const btnLogin = document.getElementById("btn-login");
    const loginError = document.getElementById("login-error");

    // DOM Elements - Dashboard
    const dashboardContainer = document.getElementById("dashboard-container");
    const profileAvatar = document.getElementById("profile-avatar");
    const profileUsername = document.getElementById("profile-username");
    const profileEmail = document.getElementById("profile-email");
    const btnLogout = document.getElementById("btn-logout");

    // DOM Elements - Buckets
    const btnFetchBuckets = document.getElementById("btn-fetch-buckets");
    const filterArchived = document.getElementById("filter-archived");
    const filterFavorite = document.getElementById("filter-favorite");
    const filterOrder = document.getElementById("filter-order");
    const selectAllBuckets = document.getElementById("select-all-buckets");
    const bucketsList = document.getElementById("buckets-list");

    // DOM Elements - Export settings & actions
    const maxArchiveSizeInput = document.getElementById("max-archive-size");
    const downloadsPathDisplay = document.getElementById("downloads-path-display");
    const btnStartExport = document.getElementById("btn-start-export");
    const btnStopExport = document.getElementById("btn-stop-export");
    const downloadsList = document.getElementById("downloads-list");

    // DOM Elements - Status & Logs
    const currentStatusText = document.getElementById("current-status-text");
    const progressWrapper = document.getElementById("progress-wrapper");
    const progressPercentVal = document.getElementById("progress-percent-val");
    const progressBarFill = document.getElementById("progress-bar-fill");
    const logsConsole = document.getElementById("logs-console");
    const btnClearLogs = document.getElementById("btn-clear-logs");

    let statusInterval = null;
    let logEventSource = null;
    let availableBuckets = [];

    // Initialize application
    init();

    async function init() {
        // Load default configuration
        try {
            const res = await fetch("/api/config");
            const config = await res.json();
            if (config.server_url) {
                serverUrlInput.value = config.server_url;
            }
            if (config.api_key) {
                apiKeyInput.value = config.api_key;
            }
            if (config.downloads_dir) {
                const suffix = config.downloads_dir === "/downloads" ? " (NAS Mount)" : "";
                downloadsPathDisplay.textContent = `${config.downloads_dir}${suffix}`;
            }
        } catch (e) {
            console.error("Failed to load configuration", e);
        }

        // Setup custom select dropdown elements
        setupCustomSelects();

        // Check active connection status
        checkStatus();
    }

    function setupCustomSelects() {
        const customSelect = document.getElementById("custom-order-select");
        if (!customSelect) return;

        const trigger = customSelect.querySelector(".custom-select-trigger");
        const valSpan = document.getElementById("custom-select-value");
        const options = customSelect.querySelectorAll(".custom-option");

        // Toggle open
        trigger.addEventListener("click", (e) => {
            e.stopPropagation();
            customSelect.classList.toggle("open");
        });

        // Close on outside click
        window.addEventListener("click", () => {
            customSelect.classList.remove("open");
        });

        // Option selection click handler
        options.forEach(opt => {
            opt.addEventListener("click", (e) => {
                e.stopPropagation();
                
                // Clear selection states
                options.forEach(o => o.classList.remove("selected"));
                opt.classList.add("selected");
                
                const val = opt.getAttribute("data-value");
                const text = opt.textContent;
                
                // Update display value
                valSpan.textContent = text;
                
                // Set native filter value and trigger change event
                filterOrder.value = val;
                filterOrder.dispatchEvent(new Event("change"));
                
                // Close list
                customSelect.classList.remove("open");
            });
        });
    }

    async function checkStatus() {
        try {
            const res = await fetch("/api/status");
            const status = await res.json();
            
            if (status.logged_in) {
                showDashboard(status.user);
                updateUIProgress(status);
                
                // Fetch default buckets and logs stream
                fetchBuckets();
                startLogStream();
                startStatusPolling();
                loadDownloads();
            } else {
                showLogin();
            }
        } catch (e) {
            showLogin();
        }
    }

    // Toggle container views
    function showDashboard(user) {
        loginContainer.classList.add("hidden");
        dashboardContainer.classList.remove("hidden");
        document.body.style.alignItems = "stretch"; // expand layout

        if (user) {
            profileUsername.textContent = user.name || "Immich User";
            profileEmail.textContent = user.email || "Connected";
            
            // Set initials for avatar
            const initials = (user.name || "AI")
                .split(" ")
                .map(n => n[0])
                .join("")
                .substring(0, 2)
                .toUpperCase();
            profileAvatar.textContent = initials;
        }
    }

    function showLogin() {
        loginContainer.classList.remove("hidden");
        dashboardContainer.classList.add("hidden");
        document.body.style.alignItems = "center";
        
        stopStatusPolling();
        stopLogStream();
    }

    // Login Form Submit handler
    loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        loginError.classList.add("hidden");
        btnLogin.querySelector("span:not(.spinner)").classList.add("hidden");
        btnLogin.querySelector(".spinner").classList.remove("hidden");
        btnLogin.disabled = true;

        const payload = {
            server_url: serverUrlInput.value,
            api_key: apiKeyInput.value,
            remember_me: rememberMeCheckbox.checked
        };

        try {
            const res = await fetch("/api/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            const data = await res.json();
            if (res.ok && data.status === "success") {
                showDashboard(data.user);
                fetchBuckets();
                startLogStream();
                startStatusPolling();
                loadDownloads();
            } else {
                loginError.textContent = data.detail || "Connection failed. Please verify credentials.";
                loginError.classList.remove("hidden");
            }
        } catch (err) {
            loginError.textContent = "Server unreachable. Make sure FastAPI server is running.";
            loginError.classList.remove("hidden");
        } finally {
            btnLogin.querySelector("span:not(.spinner)").classList.remove("hidden");
            btnLogin.querySelector(".spinner").classList.add("hidden");
            btnLogin.disabled = false;
        }
    });

    // Logout handler
    btnLogout.addEventListener("click", async () => {
        try {
            await fetch("/api/logout", { method: "POST" });
        } catch(e){}
        showLogin();
    });

    // Fetch Buckets handler
    btnFetchBuckets.addEventListener("click", fetchBuckets);
    filterArchived.addEventListener("change", fetchBuckets);
    filterFavorite.addEventListener("change", fetchBuckets);
    filterOrder.addEventListener("change", fetchBuckets);

    async function fetchBuckets() {
        btnFetchBuckets.disabled = true;
        const queryParams = new URLSearchParams({
            is_archived: filterArchived.checked,
            is_favorite: filterFavorite.checked,
            order: filterOrder.value
        });

        try {
            const res = await fetch(`/api/buckets?${queryParams}`);
            if (!res.ok) throw new Error("Failed to fetch buckets");
            
            const data = await res.json();
            availableBuckets = data.buckets || [];
            renderBucketsList();
        } catch (e) {
            console.error(e);
            bucketsList.innerHTML = `<tr><td colspan="3" class="placeholder-text error">Failed to load buckets from Immich API.</td></tr>`;
        } finally {
            btnFetchBuckets.disabled = false;
        }
    }

    function renderBucketsList() {
        if (availableBuckets.length === 0) {
            bucketsList.innerHTML = `<tr><td colspan="3" class="placeholder-text">No media buckets found matching criteria.</td></tr>`;
            btnStartExport.disabled = true;
            return;
        }

        bucketsList.innerHTML = "";
        availableBuckets.forEach(bucket => {
            const row = document.createElement("tr");
            
            // Format Bucket Time nicely
            const dateStr = bucket.timeBucket ? bucket.timeBucket.substring(0, 7) : "Unknown Date";

            row.innerHTML = `
                <td><input type="checkbox" class="bucket-checkbox" value="${bucket.timeBucket}"></td>
                <td><strong>${dateStr}</strong></td>
                <td>${bucket.count} items</td>
            `;
            bucketsList.appendChild(row);
        });

        // Re-bind row check event listeners
        const checks = document.querySelectorAll(".bucket-checkbox");
        checks.forEach(c => {
            c.addEventListener("change", updateStartExportButtonState);
        });

        selectAllBuckets.checked = false;
        updateStartExportButtonState();
    }

    // Select all checkbox
    selectAllBuckets.addEventListener("change", (e) => {
        const checks = document.querySelectorAll(".bucket-checkbox");
        checks.forEach(c => {
            c.checked = e.target.checked;
        });
        updateStartExportButtonState();
    });

    function updateStartExportButtonState() {
        const checkedCount = document.querySelectorAll(".bucket-checkbox:checked").length;
        btnStartExport.disabled = checkedCount === 0;
    }

    // Start Export Execution
    btnStartExport.addEventListener("click", async () => {
        const checkedBoxes = document.querySelectorAll(".bucket-checkbox:checked");
        const bucketIds = Array.from(checkedBoxes).map(cb => cb.value);

        if (bucketIds.length === 0) return;

        const payload = {
            bucket_ids: bucketIds,
            max_archive_size_mb: parseInt(maxArchiveSizeInput.value, 10) || 1024,
            is_archived: filterArchived.checked,
            is_favorite: filterFavorite.checked,
            order: filterOrder.value
        };

        try {
            const res = await fetch("/api/export/start", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            if (res.ok) {
                btnStartExport.classList.add("hidden");
                btnStopExport.classList.remove("hidden");
                progressWrapper.classList.remove("hidden");
            }
        } catch (e) {
            console.error("Failed to start export", e);
        }
    });

    // Stop Export Execution
    btnStopExport.addEventListener("click", async () => {
        try {
            await fetch("/api/export/stop", { method: "POST" });
        } catch (e) {
            console.error("Failed to stop export", e);
        }
    });

    // Logging Subscription (Server-Sent Events)
    function startLogStream() {
        if (logEventSource) return;

        logEventSource = new EventSource("/api/logs/stream");
        
        logEventSource.onmessage = (event) => {
            if (event.data === ":ping") return;
            
            const line = document.createElement("div");
            line.className = "log-line";
            line.textContent = event.data;
            logsConsole.appendChild(line);

            // Keep only last 500 lines in UI console to avoid memory issues
            while (logsConsole.childNodes.length > 500) {
                logsConsole.removeChild(logsConsole.firstChild);
            }

            // Autoscroll
            logsConsole.scrollTop = logsConsole.scrollHeight;
        };

        logEventSource.onerror = (err) => {
            console.error("SSE Connection failed: ", err);
            stopLogStream();
            // Retry after 5 seconds
            setTimeout(startLogStream, 5000);
        };
    }

    function stopLogStream() {
        if (logEventSource) {
            logEventSource.close();
            logEventSource = null;
        }
    }

    // Status polling
    function startStatusPolling() {
        if (statusInterval) return;
        statusInterval = setInterval(async () => {
            try {
                const res = await fetch("/api/status");
                const status = await res.json();
                updateUIProgress(status);
                
                // If download finishes, refresh downloads list
                if (!status.is_downloading) {
                    btnStartExport.classList.remove("hidden");
                    btnStopExport.classList.add("hidden");
                    loadDownloads();
                } else {
                    btnStartExport.classList.add("hidden");
                    btnStopExport.classList.remove("hidden");
                    progressWrapper.classList.remove("hidden");
                }
            } catch (e) {
                console.error("Error fetching status", e);
            }
        }, 2000);
    }

    function stopStatusPolling() {
        if (statusInterval) {
            clearInterval(statusInterval);
            statusInterval = null;
        }
    }

    function updateUIProgress(status) {
        currentStatusText.textContent = status.status_text || "Idle";
        
        if (status.is_downloading) {
            progressWrapper.classList.remove("hidden");
            const percent = status.progress_percent || 0;
            progressPercentVal.textContent = `${percent}%`;
            progressBarFill.style.width = `${percent}%`;
        }
    }

    // Load Completed Downloads
    async function loadDownloads() {
        try {
            const res = await fetch("/api/downloads");
            const data = await res.json();
            
            if (!data.files || data.files.length === 0) {
                downloadsList.innerHTML = `<div class="placeholder-text">No archives completed yet.</div>`;
                return;
            }

            downloadsList.innerHTML = "";
            data.files.forEach(file => {
                const item = document.createElement("div");
                item.className = "download-item";
                item.innerHTML = `
                    <div class="download-info">
                        <span class="download-name" title="${file.name}">${file.name}</span>
                        <span class="download-size">${file.size_formatted}</span>
                    </div>
                    <a href="/api/downloads/${encodeURIComponent(file.name)}" download class="btn-download">Download</a>
                `;
                downloadsList.appendChild(item);
            });
        } catch (e) {
            console.error("Failed to load downloads list", e);
            downloadsList.innerHTML = `<div class="placeholder-text error">Failed to load list.</div>`;
        }
    }

    // Clear logs button
    btnClearLogs.addEventListener("click", () => {
        logsConsole.innerHTML = '<div class="log-line system-msg">Console logs cleared in UI.</div>';
    });
});
