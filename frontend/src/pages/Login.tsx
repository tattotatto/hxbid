import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Typography,
  Card,
  Form,
  Input,
  Button,
  Space,
  message,
} from 'antd'
import { UserOutlined, LockOutlined } from '@ant-design/icons'
import client from '../api/client'
import Copyright from '../components/Copyright'

const { Title, Text } = Typography

interface LoginFormValues {
  username: string
  password: string
}

export default function Login() {
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  const handleSubmit = async (values: LoginFormValues) => {
    setLoading(true)
    try {
      const res = await client.post('/auth/login', {
        username: values.username,
        password: values.password,
      })
      localStorage.setItem('token', res.data.access_token)
      localStorage.setItem('user', JSON.stringify(res.data.user))
      message.success('登录成功')
      navigate('/')
    } catch (err: any) {
      const detail = err.response?.data?.detail
      message.error(detail || '登录失败，请检查用户名和密码')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        background: '#f0f2f5',
      }}
    >
      <Card style={{ width: 400 }}>
        <Space
          direction="vertical"
          size="middle"
          style={{ width: '100%', textAlign: 'center' }}
        >
          <div>
            <Title level={2} style={{ marginBottom: 0 }}>
              宏曦标书
            </Title>
            <Text type="secondary">AI 驱动投标书自动生成系统</Text>
          </div>

          <Form<LoginFormValues>
            onFinish={handleSubmit}
            layout="vertical"
            requiredMark={false}
          >
            <Form.Item
              name="username"
              rules={[{ required: true, message: '请输入用户名' }]}
            >
              <Input
                prefix={<UserOutlined />}
                placeholder="用户名"
                size="large"
              />
            </Form.Item>

            <Form.Item
              name="password"
              rules={[{ required: true, message: '请输入密码' }]}
            >
              <Input.Password
                prefix={<LockOutlined />}
                placeholder="密码"
                size="large"
              />
            </Form.Item>

            <Form.Item style={{ marginBottom: 12 }}>
              <Button
                type="primary"
                htmlType="submit"
                block
                size="large"
                loading={loading}
              >
                登录
              </Button>
            </Form.Item>
          </Form>

          <Copyright />
        </Space>
      </Card>
    </div>
  )
}
