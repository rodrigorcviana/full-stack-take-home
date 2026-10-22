<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

defineProps({ selectedId: { type: Number, default: null } })
const emit = defineEmits(['select'])

const patients = ref([])
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    patients.value = await api.listPatients()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section>
    <h2>Patients ({{ patients.length }})</h2>
    <p v-if="loading">Loading…</p>
    <p v-else-if="error">Could not load patients: {{ error }}</p>
    <p v-else-if="!patients.length">No patients migrated yet. Run <code>task import</code>.</p>
    <table v-else border="1" cellpadding="4">
      <thead>
        <tr><th>Name</th><th>Age</th><th>Gender</th><th>Observations</th></tr>
      </thead>
      <tbody>
        <tr
          v-for="p in patients"
          :key="p.id"
          :style="{ cursor: 'pointer', fontWeight: p.id === selectedId ? 'bold' : 'normal' }"
          @click="emit('select', p.id)"
        >
          <td>{{ p.name }}</td>
          <td>{{ p.age ?? '—' }}</td>
          <td>{{ p.gender ?? '—' }}</td>
          <td>{{ p.observation_count }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
