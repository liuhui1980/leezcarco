// ===================================================
// Jenkinsfile — TikTok Monitor CI/CD Pipeline
//
// 流程：
//   1. 拉取代码
//   2. 构建 Docker 镜像
//   3. 推送到私有镜像仓库 192.168.2.111:80/car/leezcarco
//   4. SSH 到目标服务器执行 deploy.sh
//
// 需要在 Jenkins 中配置以下 Credentials：
//   - registry-credentials: 镜像仓库用户名/密码（Username with password）
//   - deploy-server-ssh: 部署服务器 SSH 凭据（SSH Username with private key）
// ===================================================

pipeline {
    agent any

    environment {
        REGISTRY      = '192.168.2.111:80'
        IMAGE_REPO    = 'car/leezcarco'
        IMAGE_FULL    = "${REGISTRY}/${IMAGE_REPO}"
        DEPLOY_HOST   = '部署服务器IP'    // ← 改成你的部署服务器 IP
        DEPLOY_USER   = 'root'            // ← 改成你的 SSH 用户名
        DEPLOY_DIR    = '/opt/leezcarco'  // ← 服务器上的部署目录
    }

    stages {
        // ---- Stage 1: 拉取代码 ----
        stage('Checkout') {
            steps {
                echo "拉取代码..."
                checkout scm
            }
        }

        // ---- Stage 2: 构建 + 推送镜像 ----
        stage('Build & Push') {
            steps {
                withCredentials([usernamePassword(
                    credentialsId: 'registry-credentials',
                    usernameVariable: 'REGISTRY_USER',
                    passwordVariable: 'REGISTRY_PASS'
                )]) {
                    sh '''
                        chmod +x build-jenkins.sh
                        ./build-jenkins.sh
                    '''
                }
            }
        }

        // ---- Stage 3: 部署到服务器 ----
        stage('Deploy') {
            steps {
                sshagent(credentials: ['deploy-server-ssh']) {
                    sh """
                        # 把 deploy.sh 上传到服务器
                        scp -o StrictHostKeyChecking=no deploy.sh ${DEPLOY_USER}@${DEPLOY_HOST}:${DEPLOY_DIR}/deploy.sh

                        # SSH 执行部署
                        ssh -o StrictHostKeyChecking=no ${DEPLOY_USER}@${DEPLOY_HOST} '
                            chmod +x ${DEPLOY_DIR}/deploy.sh
                            ${DEPLOY_DIR}/deploy.sh build-${BUILD_NUMBER}
                        '
                    """
                }
            }
        }
    }

    // ---- 构建后通知（可选） ----
    post {
        success {
            echo "✅ 构建部署成功！镜像：${IMAGE_FULL}:build-${BUILD_NUMBER}"
        }
        failure {
            echo "❌ 构建部署失败，请检查日志"
        }
    }
}
