<script setup>
import { ref } from 'vue'
import PatientList from './components/PatientList.vue'
import PatientDetail from './components/PatientDetail.vue'

const selectedId = ref(null)
</script>

<template>
  <main>
    <h1>Migrated patients</h1>
    <div class="columns">
      <PatientList class="list" :selected-id="selectedId" @select="selectedId = $event" />
      <aside class="detail">
        <PatientDetail v-if="selectedId !== null" :patient-id="selectedId" @close="selectedId = null" />
        <p v-else>Select a patient to see their observations.</p>
      </aside>
    </div>
  </main>
</template>

<style>
.columns { display: flex; gap: 24px; align-items: flex-start; }
.list { flex: 0 0 auto; }
.detail { flex: 1 1 auto; position: sticky; top: 0; max-height: 100vh; overflow: auto; }
</style>
