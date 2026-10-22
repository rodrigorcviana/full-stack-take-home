<script setup>
import { ref, watch } from 'vue'
import { api } from '../api'

const props = defineProps({ patientId: { type: Number, required: true } })
const emit = defineEmits(['close'])

const patient = ref(null)
const loading = ref(false)
const error = ref('')

async function load(id) {
  loading.value = true
  error.value = ''
  try {
    patient.value = await api.getPatient(id)
  } catch (e) {
    patient.value = null
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function formatValue(o) {
  if (o.value_type === 'quantity' || o.value_type === 'integer') return `${o.value_number} ${o.value_unit}`.trim()
  if (o.value_type === 'components') return o.components.map((c) => `${c.display ?? c.code}: ${c.value} ${c.unit ?? ''}`.trim()).join(', ')
  if (o.value_type === 'none') return '—'
  return o.value_text
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString() : '—'
}

watch(() => props.patientId, load, { immediate: true })
</script>

<template>
  <section>
    <h2>Patient <template v-if="patient">{{ patient.name }}</template></h2>
    <p v-if="loading">Loading…</p>
    <p v-else-if="error">Error: {{ error }}</p>
    <template v-else-if="patient">
      <p>Age: {{ patient.age ?? '—' }} · Gender: {{ patient.gender ?? '—' }} <button @click="emit('close')">Close</button></p>
      <h3>Observations ({{ patient.observations.length }})</h3>
      <p v-if="!patient.observations.length">No observations for this patient.</p>
      <table v-else border="1" cellpadding="4">
        <thead>
          <tr><th>Date</th><th>Category</th><th>Observation</th><th>Code</th><th>Value</th><th>Status</th></tr>
        </thead>
        <tbody>
          <tr v-for="o in patient.observations" :key="o.id">
            <td>{{ formatDate(o.effective_at) }}</td>
            <td>{{ o.category || '—' }}</td>
            <td>{{ o.display || '—' }}</td>
            <td>{{ o.code || '—' }}</td>
            <td>{{ formatValue(o) }}</td>
            <td>{{ o.status }}</td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>
