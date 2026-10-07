(() => {
  const state = { revision: null, subject: null, bundle: null, bundleText: null, plan: null };
  const $ = (id) => document.getElementById(id);
  const status = (message, error = false) => {
    const node = $('status');
    node.textContent = message || '';
    node.className = error ? 'error' : '';
  };
  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    const payload = await response.json();
    if (!payload.ok) throw new Error(payload.error?.message || 'Request failed');
    return payload.data;
  }
  const pretty = (value) => JSON.stringify(value, null, 2);
  const esc = (value) =>
    String(value ?? '').replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
    );
  function sync(snapshot) {
    if (!snapshot) return;
    state.revision = snapshot.world_revision ?? state.revision;
    state.subject = snapshot.world?.subject_id ?? state.subject;
    $('revision').textContent = `Applied revision ${state.revision ?? '—'}`;
    if (snapshot.world) renderWorld(snapshot.world);
    if (snapshot.evidence) renderEvidence(snapshot.evidence);
    if (snapshot.recall)
      $('recall').textContent = pretty(snapshot.recall.preview ?? snapshot.recall);
  }
  async function loadWorld() {
    status('Loading World…');
    const data = await api('/api/world');
    state.revision = data.world_revision;
    state.subject = data.subject_id;
    $('revision').textContent = `Applied revision ${state.revision}`;
    renderWorld(data);
    status('');
  }
  function renderWorld(data) {
    const buckets = { entity: [], relationship: [], event: [], cognition: [] };
    for (const item of data.items || []) (buckets[item.object_kind] || []).push(item);
    $('world-groups').innerHTML = Object.entries(buckets)
      .map(
        ([kind, items]) =>
          `<section class="group"><h2>${esc(kind)} · ${items.length}</h2>${items.length ? items.map((item) => `<button class="card" data-kind="${esc(kind)}" data-id="${esc(item.item_id)}"><strong>${esc(item.value?.content || item.value?.canonical_name || item.item_id)}</strong><div class="meta">${esc(item.current_state)} · revision ${esc(item.world_revision)}</div></button>`).join('') : '<p class="meta">No current items.</p>'}</section>`,
      )
      .join('');
    document
      .querySelectorAll('[data-kind]')
      .forEach((node) =>
        node.addEventListener('click', () => detail(node.dataset.kind, node.dataset.id)),
      );
  }
  function confirmAction(button, action) {
    if (button.dataset.confirmed === 'true') {
      action();
      return;
    }
    button.dataset.confirmed = 'true';
    button.dataset.originalLabel = button.textContent;
    button.textContent = `Confirm ${button.dataset.originalLabel}`;
    status(`Select ${button.textContent} to continue.`);
  }
  function renderEvidence(data) {
    $('evidence-list').innerHTML =
      (data.evidence || [])
        .map((ev) => {
          const lifecycle = ev.lifecycle || {},
            permissions = ev.permissions || {};
          return `<article class="card"><strong>${esc(ev.raw_content || ev.summary || ev.evidence_id)}</strong><div class="meta">${esc(ev.source_kind)} · ${esc(ev.occurred_at)} · currentness ${esc(ev.currentness_state)}</div><div class="meta">tombstone deleted_at ${esc(lifecycle.deleted_at ?? 'not tombstoned')} · invalid_at ${esc(lifecycle.invalid_at ?? '—')} · archived_at ${esc(lifecycle.archived_at ?? '—')} · muted_at ${esc(lifecycle.muted_at ?? '—')}</div><fieldset class="permission-form"><legend>Evidence permissions</legend><label><input type="checkbox" data-permission-local ${permissions.allow_local_read ? 'checked' : ''}> Allow local read</label><label><input type="checkbox" data-permission-cloud ${permissions.allow_cloud_read ? 'checked' : ''}> Allow cloud read</label><label><input type="checkbox" data-permission-inference ${permissions.allow_inference ? 'checked' : ''}> Allow inference</label><button data-permission-save="${esc(ev.evidence_id)}">Save permissions</button></fieldset><div class="control"><button data-forget="${esc(ev.evidence_id)}">Forget</button></div></article>`;
        })
        .join('') || '<p class="meta">No Evidence.</p>';
    document
      .querySelectorAll('[data-forget]')
      .forEach(
        (node) =>
          (node.onclick = () =>
            confirmAction(node, () =>
              command('forget_evidence', 'evidence', node.dataset.forget, {}),
            )),
      );
    document.querySelectorAll('[data-permission-save]').forEach(
      (node) =>
        (node.onclick = () => {
          const card = node.closest('article'),
            checked = (name) => Boolean(card?.querySelector(`[data-permission-${name}]`)?.checked);
          command('update_evidence_permissions', 'evidence', node.dataset.permissionSave, {
            allow_local_read: checked('local'),
            allow_cloud_read: checked('cloud'),
            allow_inference: checked('inference'),
          });
        }),
    );
  }
  async function detail(kind, id) {
    try {
      const data = await api(`/api/world/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`);
      const canCorrect = ['relationship', 'event', 'cognition'].includes(kind);
      $('detail-content').innerHTML =
        `<h2>${esc(kind)}</h2><pre>${esc(pretty(data))}</pre>${canCorrect ? '<div class="form-row"><label for="correction-text">Corrected value</label><textarea id="correction-text" rows="3"></textarea><button id="correct-submit">Save correction</button></div><div class="control"><button id="retract">Retract</button>' : '<p class="meta">Entity correction and retraction are unavailable.</p>'}<div class="control"><button id="archive">Archive</button><button id="mute">Mute</button></div>`;
      $('detail').showModal();
      if (canCorrect) {
        $('correct-submit').onclick = () => {
          const text = $('correction-text').value.trim();
          if (text) command('correct_world_item', kind, id, { correction_text: text });
          else status('Enter a corrected value before saving.', true);
        };
        $('retract').onclick = () =>
          confirmAction($('retract'), () => command('retract_world_item', kind, id, {}));
      }
      $('archive').onclick = () =>
        confirmAction($('archive'), () => command('archive_world_item', kind, id, {}));
      $('mute').onclick = () =>
        confirmAction($('mute'), () => command('mute_world_item', kind, id, {}));
    } catch (error) {
      status(error.message, true);
    }
  }
  async function command(operation, target_kind, target_id, payload) {
    try {
      const command = {
        schema_version: 1,
        command_id: crypto.randomUUID(),
        subject_id: state.subject,
        actor: 'memory-experience',
        expected_world_revision: state.revision,
        operation,
        target_kind,
        target_id,
        payload,
        submitted_at: new Date().toISOString(),
      };
      const data = await api('/api/commands', {
        method: 'POST',
        body: JSON.stringify({ command, recall_query: $('recall-query').value || 'memory' }),
      });
      status(`Receipt ${data.receipt.command_id}: ${data.receipt.result_state}`);
      sync(data.refresh);
      $('detail').close();
    } catch (error) {
      status(error.message, true);
    }
  }
  async function loadEvidence() {
    const data = await api('/api/evidence');
    renderEvidence(data);
  }
  async function loadJobs() {
    const data = await api('/api/jobs');
    $('jobs-list').innerHTML = `<pre>${esc(pretty(data.jobs || []))}</pre>`;
  }
  async function loadClarifications() {
    const data = await api('/api/clarifications?state=open');
    $('clarification-list').innerHTML =
      (data.clarifications || [])
        .map(
          (c) =>
            `<article class="card"><strong>${esc(c.question)}</strong><div class="meta">${esc(c.clarification_id)} · ${esc(c.result_session_id)}</div><label>Answer<textarea data-answer-text rows="3"></textarea></label><button data-answer="${esc(c.clarification_id)}" data-session="${esc(c.result_session_id)}">Answer</button></article>`,
        )
        .join('') || '<p class="meta">No open clarifications.</p>';
    document.querySelectorAll('[data-answer]').forEach(
      (node) =>
        (node.onclick = async () => {
          const answer =
            node.closest('article')?.querySelector('[data-answer-text]')?.value.trim() || '';
          if (!answer) {
            status('Enter an answer before sending.', true);
            return;
          }
          try {
            const data = await api(`/api/clarifications/${node.dataset.answer}/answer`, {
              method: 'POST',
              body: JSON.stringify({ result_session_id: node.dataset.session, answer }),
            });
            const receipt = data.receipt;
            status(
              `Clarification receipt ${receipt.clarification_id} · Evidence ${receipt.answer_evidence_id} · follow-up Job ${receipt.follow_up_job_id}`,
            );
            sync(data.refresh);
            loadClarifications();
          } catch (error) {
            status(error.message, true);
          }
        }),
    );
  }
  document.querySelectorAll('nav button').forEach(
    (button) =>
      (button.onclick = async () => {
        document.querySelectorAll('nav button').forEach((n) => n.classList.remove('active'));
        button.classList.add('active');
        document.querySelectorAll('.view').forEach((n) => n.classList.add('hidden'));
        $(button.dataset.view).classList.remove('hidden');
        try {
          ({
            world: loadWorld,
            evidence: loadEvidence,
            jobs: loadJobs,
            clarifications: loadClarifications,
          })[button.dataset.view]?.();
        } catch (error) {
          status(error.message, true);
        }
      }),
  );
  $('detail-close').onclick = () => $('detail').close();
  $('recall-button').onclick = async () => {
    try {
      $('recall').textContent = pretty(
        await api(`/api/recall?q=${encodeURIComponent($('recall-query').value || 'memory')}`),
      );
    } catch (error) {
      status(error.message, true);
    }
  };
  function portablePlanText(plan) {
    const conflicts = Array.isArray(plan.conflicts) ? plan.conflicts : [];
    return `${pretty(plan)}\n\nConflict preview (${conflicts.length}):\n${pretty(conflicts)}`;
  }
  function portableBundleBody(planHash) {
    if (typeof state.bundleText !== 'string')
      throw new Error('Select a valid Portable bundle first.');
    const suffix = planHash === undefined ? '' : `,"plan_hash":${JSON.stringify(planHash)}`;
    return `{"bundle":${state.bundleText}${suffix}}`;
  }
  $('portable-export').onclick = async () => {
    try {
      const data = await api('/api/portable/export', { method: 'POST', body: '{}' });
      $('portable-output').textContent = pretty(data);
    } catch (error) {
      status(error.message, true);
    }
  };
  $('portable-file').onchange = async (event) => {
    try {
      const bundleText = await event.target.files[0].text(),
        bundle = JSON.parse(bundleText);
      if (!bundle || typeof bundle !== 'object' || Array.isArray(bundle))
        throw new Error('bundle_object_required');
      state.bundleText = bundleText;
      state.bundle = bundle;
      $('portable-plan').disabled = false;
      $('portable-output').textContent = 'Bundle loaded. Create a dry-run plan.';
    } catch (error) {
      state.bundleText = null;
      state.bundle = null;
      $('portable-plan').disabled = true;
      $('portable-apply').disabled = true;
      status('The selected file is not a valid JSON bundle.', true);
    }
  };
  $('portable-plan').onclick = async () => {
    try {
      state.plan = await api('/api/portable/plan', { method: 'POST', body: portableBundleBody() });
      $('portable-output').textContent = portablePlanText(state.plan);
      $('portable-apply').disabled = !state.plan.valid;
    } catch (error) {
      status(error.message, true);
    }
  };
  $('portable-apply').onclick = async () => {
    try {
      const data = await api('/api/portable/apply', {
        method: 'POST',
        body: portableBundleBody(state.plan.plan_hash),
      });
      status(`Portable receipt ${data.receipt.receipt_id}: ${data.receipt.result_state}`);
      sync(data.refresh);
    } catch (error) {
      status(error.message, true);
    }
  };
  loadWorld().catch((error) => status(error.message, true));
})();
