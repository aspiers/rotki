<template>
  <v-card>
    <v-card-title>
      {{ title }}
      <v-spacer />
      <premium-lock />
    </v-card-title>
  </v-card>
</template>

<script lang="ts">
import { Component, Prop, Vue } from 'vue-property-decorator';
import PremiumLock from '../premium/PremiumLock.vue';

@Component({
  components: { PremiumLock }
})
export default class PremiumCard extends Vue {
  @Prop({ required: true })
  title!: string;
}
</script>

<style scoped></style>
