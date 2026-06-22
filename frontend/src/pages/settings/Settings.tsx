import { useEffect } from 'react'
import { Card, Form, Input, Select, Button, message } from 'antd'

const AI_PROVIDERS = [
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'tongyi', label: '通义千问' },
]

const AI_FORM_KEY = 'ai_settings'
const NOTIFY_FORM_KEY = 'notify_settings'
const SETTINGS_STORAGE_KEY = 'settings'

interface AISettings {
  ai_provider: string
  api_key: string
  temperature: number
}

interface NotifySettings {
  wecom_webhook: string
  dingtalk_webhook: string
}

interface AppSettings {
  ai?: AISettings
  notify?: NotifySettings
}

function loadSettings(): AppSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_STORAGE_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

function saveSettings(values: AppSettings): void {
  const existing = loadSettings()
  localStorage.setItem(
    SETTINGS_STORAGE_KEY,
    JSON.stringify({ ...existing, ...values }),
  )
}

export default function Settings() {
  const [aiForm] = Form.useForm()
  const [notifyForm] = Form.useForm()

  useEffect(() => {
    const settings = loadSettings()
    if (settings.ai) {
      aiForm.setFieldsValue(settings.ai)
    }
    if (settings.notify) {
      notifyForm.setFieldsValue(settings.notify)
    }
  }, [])

  const handleAISave = () => {
    const values = aiForm.getFieldsValue()
    saveSettings({ ai: values })
    message.success('AI 配置已保存')
  }

  const handleNotifySave = () => {
    const values = notifyForm.getFieldsValue()
    saveSettings({ notify: values })
    message.success('通知配置已保存')
  }

  return (
    <div>
      <Card title="AI 模型配置" style={{ maxWidth: 600, marginBottom: 24 }}>
        <Form
          form={aiForm}
          layout="vertical"
          initialValues={{ temperature: 0.7 }}
        >
          <Form.Item label="AI 提供商" name="ai_provider">
            <Select options={AI_PROVIDERS} placeholder="选择 AI 提供商" />
          </Form.Item>
          <Form.Item label="API Key" name="api_key">
            <Input.Password placeholder="输入 API Key" />
          </Form.Item>
          <Form.Item label="Temperature" name="temperature">
            <Input
              type="number"
              min={0}
              max={1}
              step={0.1}
              placeholder="0.7"
            />
          </Form.Item>
          <Form.Item>
            <Button type="primary" onClick={handleAISave}>
              保存配置
            </Button>
          </Form.Item>
        </Form>
      </Card>

      <Card title="通知配置" style={{ maxWidth: 600 }}>
        <Form form={notifyForm} layout="vertical">
          <Form.Item label="企业微信 Webhook" name="wecom_webhook">
            <Input placeholder="企业微信机器人 Webhook URL" />
          </Form.Item>
          <Form.Item label="钉钉 Webhook" name="dingtalk_webhook">
            <Input placeholder="钉钉机器人 Webhook URL" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" onClick={handleNotifySave}>
              保存配置
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )
}
