const path = require('node:path');

const srcDir = path.join(__dirname, 'src');
const localesDir = path.join(srcDir, 'locales');

module.exports = {
  extends: [
    './.eslintrc-auto-import.json',
    'plugin:@intlify/vue-i18n/recommended'
  ],
  rules: {
    '@intlify/vue-i18n/no-unused-keys': [
      'error',
      {
        extensions: ['.ts', '.vue'],
        ignores: ['/transactions.query_status.*/', '/premium_components.*/']
      }
    ],
    '@intlify/vue-i18n/no-duplicate-keys-in-locale': 'error',
    '@intlify/vue-i18n/key-format-style': [
      'error',
      'snake_case',
      {
        allowArray: false
      }
    ],
    '@intlify/vue-i18n/no-raw-text': [
      process.env.NODE_ENV === 'development' ? 'warn' : 'error',
      {
        ignoreNodes: ['md-icon', 'v-icon'],
        ignorePattern: '^[-#:()&/+]+$',
        ignoreText: ['EUR', 'HKD', 'USD']
      }
    ]
  },
  overrides: [
    {
      files: ['*.ts'],
      extends: ['@rotki/eslint-config-ts']
    },
    {
      // Disabled because it moves the inheritAttrs export to the setup script
      files: ['AmountInput.vue'],
      rules: {
        'import/first': 'off'
      }
    }
  ],
  settings: {
    'vue-i18n': {
      localeDir: `${path.resolve(localesDir)}/*.{json,json5,yaml,yml}`,
      messageSyntaxVersion: '^9.0.0'
    }
  }
};
