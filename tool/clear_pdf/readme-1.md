# 自动擦除手写文字功能描述

手写文字擦除

请求URL
https://api.textin.com/ai/service/v1/handwritten_erase

HTTP请求方法（Method）
HTTP POST

请求头说明（Request Headers）
请在HTTP请求中添加以下自定义标头（Header）。

header 名	值
x-ti-app-id	请登录后前往 “工作台-账号设置-开发者信息” 查看 x-ti-app-id
x-ti-secret-code	请登录后前往 “工作台-账号设置-开发者信息” 查看 x-ti-secret-code
URL参数（Parameters）
URL参数指以 {参数名}={参数值} 形式拼接到 URL 上的键值对。它以 ? 开头，不同参数之间使用 & 连接。形如 ?p1=v1&p2=v2
参数名	数据类型	是否必填	允许的值	描述
crop	integer	否	0, 1	
0 关闭切边操作，默认为0
1 执行切边操作
crop_position	string	否	见描述	
支持客户端传入原图对应切边坐标进行切边; 默认为自动切边坐标点； 调用时需执行切边操作(crop=1); 格式 x1,y1,x2,y2,x3,y3,x4,y4

(x1, y1) 左上角坐标
(x2, y2) 右上角坐标
(x3, y3) 右下角坐标
(x4, y4) 左下角坐标
doc_direction	integer	否	0, 1, 2, 3, 4	
支持客户端传入旋转角度和自动判断方向并旋转，默认不旋转

0 关闭方向转正
1 顺时针旋转90度
2 顺时针旋转180度
3 顺时针旋转270度
4 自动方向转正
mask_position	string	否	见描述	
支持客户端传入原图对应擦除坐标点进行擦除; 默认整图区域; 格式 x1,y1,x2,y2,x3,y3,x4,y4

(x1, y1) 左上角坐标
(x2, y2) 右上角坐标
(x3, y3) 右下角坐标
(x4, y4) 左下角坐标
dewarp	integer	否	0, 1	
0 不执行弯曲矫正
1 执行弯曲矫正， 默认为1
binarization	integer	否	0, 1	
0 不执行增强锐化滤镜
1 执行增强锐化滤镜， 默认为1
image_type	integer	否	0, 1	
0 返回黑白图像
1 返回彩色图像， 默认为1
请求体说明（Request Body）
支持以下两种请求格式

1. Content-Type: application/octet-stream

要上传的图片，目前支持jpg, png, bmp, pdf, tiff, webp, 单帧gif等大部分格式。

请注意，请求体的数据格式为本地文件的二进制流，非 FormData 或其他格式。文件大小不超过 50M，图像宽高须介于 20 和 10000（像素）之间。

2. Content-Type: text/plain

请求体的数据格式为文本，内容为在线文件的URL链接（支持http以及https协议）。在线文件大小不超过 50M，图像宽高须介于 20 和 10000（像素）之间。

响应体说明 （Response）
Content-Type: application/json

JSON结构说明如下：

说明：所有接口响应中均包含字段 x_request_id（string类型），作为请求的唯一标识。

字段名	类型	描述
code	integer	错误码，详见“错误码说明”
message	string	
错误信息

version	string	
接口版本号

duration	number	
接口耗时计算，时间单位是毫秒(ms)

result	object	
  + image	string	
图像处理后的jpg图片，base64格式

JSON结构示例
{
"code":200,
"message":"success",
"version":"1.0.0",
"duration":100,
"result":{
"image":"/9j/4AAQSkZJRgABAQAAAQABAAD/2wBD"
}
}
错误码说明
错误码	描述
40101	x-ti-app-id 或 x-ti-secret-code 为空
40102	x-ti-app-id 或 x-ti-secret-code 无效，验证失败
40103	客户端IP不在白名单
40003	余额不足，请充值后再使用
40004	参数错误，请查看技术文档，检查传参
40007	机器人不存在或未发布
40008	机器人未开通，请至市场开通后重试
40301	文件类型不支持，接口会返回实际检测到的文件类型，如“当前文件类型为.gif”
40302	上传文件大小不符，文件大小不超过 50M
40303	文件类型不支持
40304	图片尺寸不符，图像宽高须介于 20 和 10000（像素）之间
40305	识别文件未上传
40306	QPS超过限制，收到此状态码时请勿重试，持续请求可能触发IP流控，如需扩容请联系商务
40400	无效的请求链接，请检查链接是否正确
30203	基础服务故障，请稍后重试
500	服务器内部错误



# 示例代码


import requests
import json

def get_file_content(filePath):
    with open(filePath, 'rb') as fp:
        return fp.read()

class CommonOcr(object):
    def __init__(self, img_path=None, is_url=False):
        # 自动擦除手写文字
        self._url = 'https://api.textin.com/ai/service/v1/handwritten_erase'
        # 请登录后前往 “工作台-账号设置-开发者信息” 查看 x-ti-app-id
        # 示例代码中 x-ti-app-id 非真实数据
        self._app_id = 'c81f*************************e9ff'
        # 请登录后前往 “工作台-账号设置-开发者信息” 查看 x-ti-secret-code
        # 示例代码中 x-ti-secret-code 非真实数据
        self._secret_code = '5508***********************1c17'
        self._img_path = img_path
        self._is_url = is_url

    def recognize(self):
        head = {}
        try:
            head['x-ti-app-id'] = self._app_id
            head['x-ti-secret-code'] = self._secret_code
            if self._is_url:
                head['Content-Type'] = 'text/plain'
                body = self._img_path
            else:
                image = get_file_content(self._img_path)
                head['Content-Type'] = 'application/octet-stream'
                body = image
            result = requests.post(self._url, data=body, headers=head)
            return result.text
        except Exception as e:
            return e

if __name__ == "__main__":
    # 示例 1：传输文件
    response = CommonOcr(img_path=r'example.jpg')
    print(response.recognize())
    # 示例 2：传输 URL
    response = CommonOcr(img_path='http://example.com/example.jpg', is_url=True)
    print(response.recognize())


# API Key信息

x-ti-app-id：8726f9e642bb6329c2cfd76b5a500012

x-ti-secret-code：76e9553c0de866614d2bff5434a86392

