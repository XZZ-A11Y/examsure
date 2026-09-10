from flask import Flask, request, jsonify, render_template, session, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_socketio import SocketIO, join_room, leave_room, send, emit
import os
import datetime
import uuid
import jwt # type: ignore
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///e:/examsure/config/database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# 使用绝对路径确保文件保存位置正确
import os
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../uploads')
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = False

CORS(app, supports_credentials=True, origins=['*'])
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins='*')

# 定义数据库模型
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    is_member = db.Column(db.Boolean, default=False)
    member_start = db.Column(db.DateTime)
    member_end = db.Column(db.DateTime)
    member_type = db.Column(db.String(20))

class PaperCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('paper_category.id'))
    children = db.relationship('PaperCategory', backref=db.backref('parent', remote_side=[id]))

class Paper(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    file_path = db.Column(db.String(300), nullable=False)
    preview_image = db.Column(db.String(300), nullable=True)  # 预览图路径
    category_id = db.Column(db.Integer, db.ForeignKey('paper_category.id'))
    upload_time = db.Column(db.DateTime, default=datetime.datetime.now)
    # 价格相关字段
    price_type = db.Column(db.String(20), default='free_all')  # free_all: 全员免费, free_member: 会员免费, paid: 全部收费
    original_price = db.Column(db.Float, default=0.0)  # 原价
    # 统计相关字段
    download_count = db.Column(db.Integer, default=0)  # 下载次数
    category = db.relationship('PaperCategory', backref=db.backref('papers', lazy=True))

class PaperPurchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    paper_id = db.Column(db.Integer, db.ForeignKey('paper.id'))
    price = db.Column(db.Float, nullable=False)  # 实际支付价格
    status = db.Column(db.String(20), default='pending')  # pending: 待确认, confirmed: 已确认
    create_time = db.Column(db.DateTime, default=datetime.datetime.now)
    confirm_time = db.Column(db.DateTime)
    user = db.relationship('User', backref=db.backref('purchases', lazy=True))
    paper = db.relationship('Paper', backref=db.backref('purchases', lazy=True))

class PaymentRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    member_type = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='pending')
    create_time = db.Column(db.DateTime, default=datetime.datetime.now)
    user = db.relationship('User', backref=db.backref('payment_requests', lazy=True))

class PasswordReset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    token = db.Column(db.String(100), unique=True, nullable=False)
    expire_time = db.Column(db.DateTime, nullable=False)
    create_time = db.Column(db.DateTime, default=datetime.datetime.now)
    user = db.relationship('User', backref=db.backref('password_resets', lazy=True))

class VerificationCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(10), nullable=False)
    create_time = db.Column(db.DateTime, default=datetime.datetime.now)
    expire_time = db.Column(db.DateTime, nullable=False)

class DownloadRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    paper_id = db.Column(db.Integer, db.ForeignKey('paper.id'))
    download_time = db.Column(db.DateTime, default=datetime.datetime.now)
    user = db.relationship('User', backref=db.backref('downloads', lazy=True))
    paper = db.relationship('Paper', backref=db.backref('download_records', lazy=True))

# 聊天消息表
class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    sender_type = db.Column(db.String(20), nullable=False)  # 'user' 或 'admin'
    message = db.Column(db.Text, nullable=False)
    send_time = db.Column(db.DateTime, default=datetime.datetime.now)
    status = db.Column(db.String(20), default='unread')  # 'unread' 或 'read'
    room_id = db.Column(db.Integer, db.ForeignKey('chat_room.id'))  # 聊天房间ID，0表示私聊
    sender = db.relationship('User', backref=db.backref('messages', lazy=True))
    room = db.relationship('ChatRoom', backref=db.backref('messages', lazy=True))

# 聊天房间表
class ChatRoom(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    creator = db.relationship('User', backref=db.backref('created_rooms', lazy=True))
    is_group = db.Column(db.Boolean, default=True)  # True表示群聊，False表示私聊

# 房间成员表
class RoomMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey('chat_room.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    joined_at = db.Column(db.DateTime, default=datetime.datetime.now)
    room = db.relationship('ChatRoom', backref=db.backref('members', lazy=True))
    user = db.relationship('User', backref=db.backref('rooms', lazy=True))
    __table_args__ = (db.UniqueConstraint('room_id', 'user_id', name='_room_user_uc'),)

# 好友关系表
class FriendRelation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    friend_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)
    status = db.Column(db.String(20), default='accepted')  # 'pending', 'accepted', 'rejected'
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('friends', lazy=True))
    friend = db.relationship('User', foreign_keys=[friend_id])
    __table_args__ = (db.UniqueConstraint('user_id', 'friend_id', name='_user_friend_uc'),)

# 初始化数据库
with app.app_context():
    db.create_all()
    # 创建管理员用户（ID=0）
    admin = User.query.get(0)
    if not admin:
        admin = User(
            id=0,
            username='管理员',
            password='admin123',
            email='admin@example.com'
        )
        db.session.add(admin)
        db.session.commit()

# 管理端登录
@app.route('/admin/login', methods=['POST'])
def admin_login():
    data = request.json
    print('登录请求数据:', data)
    print('当前session:', session)
    if data.get('password') == 'syxx130313@':
        session['admin_logged_in'] = True
        print('登录成功，设置session:', session)
        return jsonify({'success': True, 'message': '登录成功'})
    return jsonify({'success': False, 'message': '密码错误'})

# 客户端注册
@app.route('/client/register', methods=['POST'])
def client_register():
    data = request.json
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')
    
    # 检查用户名是否已存在
    if User.query.filter_by(username=username).first():
        return jsonify({'success': False, 'message': '用户名已存在'})
    
    # 检查邮箱是否已存在
    if User.query.filter_by(email=email).first():
        return jsonify({'success': False, 'message': '邮箱已注册'})
    
    new_user = User(
        username=username,
        email=email,
        password=password
    )
    db.session.add(new_user)
    db.session.commit()
    
    # 自动添加管理员为好友（管理员ID为0）
    # 创建用户到管理员的好友关系
    user_to_admin = FriendRelation(
        user_id=new_user.id,
        friend_id=0,  # 管理员ID为0
        status='accepted'
    )
    db.session.add(user_to_admin)
    
    # 创建管理员到用户的好友关系
    admin_to_user = FriendRelation(
        user_id=0,  # 管理员ID为0
        friend_id=new_user.id,
        status='accepted'
    )
    db.session.add(admin_to_user)
    
    db.session.commit()
    return jsonify({'success': True})

# 客户端登录
@app.route('/client/login', methods=['POST'])
def client_login():
    data = request.json
    user = User.query.filter_by(username=data.get('username'), password=data.get('password')).first()
    if user:
        session['user_id'] = user.id
        # 生成JWT令牌
        token = jwt.encode({
            'user_id': user.id,
            'exp': datetime.datetime.utcnow() + datetime.timedelta(days=30)
        }, app.config['SECRET_KEY'], algorithm='HS256')
        return jsonify({'success': True, 'user_id': user.id, 'token': token})
    return jsonify({'success': False, 'message': '用户名或密码错误'})

# 发送验证码
@app.route('/client/send-verification-code', methods=['POST'])
def send_verification_code():
    import random
    data = request.json
    email = data.get('email')
    
    # 检查邮箱是否已注册
    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'success': False, 'message': '该邮箱未注册'})
    
    # 生成随机6位验证码
    code = ''.join([str(random.randint(0, 9)) for _ in range(6)])
    
    # 设置验证码10分钟过期
    expire_time = datetime.datetime.now() + datetime.timedelta(minutes=10)
    
    # 保存验证码到数据库
    new_code = VerificationCode(
        email=email,
        code=code,
        expire_time=expire_time
    )
    db.session.add(new_code)
    db.session.commit()
    
    # 生成包含验证码的URL
    verification_url = f"http://192.168.1.12:5000/client/verify-code-display?code={code}"
    
    # 这里应该发送邮件，暂时先在日志中显示验证码和URL
    print(f"验证码：{code}")
    print(f"验证码URL：{verification_url}")
    
    # 返回验证码和URL给前端
    return jsonify({'success': True, 'message': '验证码已发送', 'verification_code': code, 'verification_url': verification_url})

# 显示验证码的页面
@app.route('/client/verify-code-display', methods=['GET'])
def verify_code_display():
    code = request.args.get('code')
    return f"<h1>您的验证码是：{code}</h1><p>请将此验证码输入到忘记密码页面</p>"

# 验证验证码
@app.route('/client/verify-code', methods=['POST'])
def verify_code():
    data = request.json
    email = data.get('email')
    code = data.get('code')
    
    # 查找最新的验证码
    verification = VerificationCode.query.filter_by(email=email).order_by(VerificationCode.create_time.desc()).first()
    
    if not verification:
        return jsonify({'success': False, 'message': '验证码不存在'})
    
    if verification.expire_time < datetime.datetime.now():
        return jsonify({'success': False, 'message': '验证码已过期'})
    
    if verification.code != code:
        return jsonify({'success': False, 'message': '验证码错误'})
    
    # 生成重置令牌
    token = str(uuid.uuid4())
    expire_time = datetime.datetime.now() + datetime.timedelta(hours=1)  # 1小时过期
    
    # 保存重置请求
    reset_request = PasswordReset(
        user_id=verification.user_id if hasattr(verification, 'user_id') else User.query.filter_by(email=email).first().id,
        token=token,
        expire_time=expire_time
    )
    db.session.add(reset_request)
    db.session.commit()
    
    return jsonify({'success': True, 'message': '验证码验证成功', 'token': token})

# 请求密码重置
@app.route('/client/forgot-password', methods=['POST'])
def forgot_password():
    # 这个接口可能不再需要，因为我们现在使用验证码验证方式
    return jsonify({'success': False, 'message': '请使用验证码验证方式'})

# 验证重置令牌
@app.route('/client/verify-reset-token/<token>', methods=['GET'])
def verify_reset_token(token):
    reset_request = PasswordReset.query.filter_by(token=token).first()
    if not reset_request:
        return jsonify({'success': False, 'message': '无效的重置令牌'})
    
    if reset_request.expire_time < datetime.datetime.now():
        return jsonify({'success': False, 'message': '重置令牌已过期'})
    
    return jsonify({'success': True, 'message': '令牌有效'})

# 显示重置密码页面
@app.route('/client/reset-password', methods=['GET'])
def show_reset_password_page():
    # 这个路由用于显示重置密码页面，实际页面由前端提供
    return send_from_directory('../frontend/client', 'reset-password.html')

# 重置密码
@app.route('/client/reset-password', methods=['POST'])
def reset_password():
    data = request.json
    token = data.get('token')
    new_password = data.get('new_password')
    
    reset_request = PasswordReset.query.filter_by(token=token).first()
    if not reset_request:
        return jsonify({'success': False, 'message': '无效的重置令牌'})
    
    if reset_request.expire_time < datetime.datetime.now():
        return jsonify({'success': False, 'message': '重置令牌已过期'})
    
    # 更新用户密码
    user = reset_request.user
    user.password = new_password
    
    # 删除重置请求
    db.session.delete(reset_request)
    db.session.commit()
    
    return jsonify({'success': True, 'message': '密码重置成功'})

# JWT认证装饰器
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'success': False, 'message': '缺少令牌'}), 401
        
        try:
            # 移除Bearer前缀
            if token.startswith('Bearer '):
                token = token.split(' ')[1]
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            current_user = User.query.get(data['user_id'])
        except:
            return jsonify({'success': False, 'message': '令牌无效'}), 401
        
        return f(current_user, *args, **kwargs)
    return decorated

# 获取用户会员状态
@app.route('/client/member/status', methods=['GET'])
@token_required
def get_member_status(current_user):
    if current_user and current_user.is_member and current_user.member_end > datetime.datetime.now():
        return jsonify({
            'success': True,
            'is_member': True,
            'member_end': current_user.member_end.isoformat()
        })
    return jsonify({'success': True, 'is_member': False})

# 购买会员
@app.route('/client/member/buy', methods=['POST'])
@token_required
def buy_member(current_user):
    data = request.json
    member_type = data.get('member_type')
    new_request = PaymentRequest(user_id=current_user.id, member_type=member_type)
    db.session.add(new_request)
    db.session.commit()
    return jsonify({'success': True, 'request_id': new_request.id})

# 上传试卷
@app.route('/admin/paper/upload', methods=['POST'])
def upload_paper():
    print('收到上传请求')
    print('Session:', session)
    
    if not session.get('admin_logged_in'):
        print('上传失败：未登录')
        return jsonify({'success': False, 'message': '未登录'})
    
    try:
        file = request.files['file']
        title = request.form['title']
        category_id = request.form['category_id']
        # 获取价格相关参数
        price_type = request.form.get('price_type', 'free_all')
        original_price_str = request.form.get('original_price', '0.0')
        # 处理空字符串情况
        original_price = float(original_price_str) if original_price_str else 0.0
        
        print(f'表单数据：title={title}, category_id={category_id}, price_type={price_type}, original_price={original_price}')
        print(f'文件：{file.filename}, 类型：{file.mimetype}, 大小：{file.content_length}')
        
        if file:
            filename = str(uuid.uuid4()) + '.' + file.filename.split('.')[-1]
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            print(f'保存路径：{file_path}')
            
            file.save(file_path)
            print('文件保存成功')
            
            # 处理预览图上传
            preview_image = None
            if 'preview_image' in request.files and request.files['preview_image']:
                preview_file = request.files['preview_image']
                preview_filename = str(uuid.uuid4()) + '.' + preview_file.filename.split('.')[-1]
                preview_file_path = os.path.join(app.config['UPLOAD_FOLDER'], preview_filename)
                preview_file.save(preview_file_path)
                preview_image = preview_filename
                print(f'预览图保存成功：{preview_image}')
            
            new_paper = Paper(
                title=title,
                file_path=filename,
                preview_image=preview_image,
                category_id=category_id,
                price_type=price_type,
                original_price=original_price
            )
            db.session.add(new_paper)
            db.session.commit()
            print('数据库保存成功')
            return jsonify({'success': True})
        else:
            print('上传失败：未获取到文件')
            return jsonify({'success': False, 'message': '未获取到文件'})
    except Exception as e:
        print(f'上传失败：{str(e)}')
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'上传失败：{str(e)}'})

# 获取试卷分类树
@app.route('/admin/categories', methods=['GET'])
def get_categories():
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': '未登录'})
    categories = PaperCategory.query.all()
    
    def build_tree(category):
        return {
            'id': category.id,
            'name': category.name,
            'children': [build_tree(child) for child in category.children]
        }
    
    root_categories = [cat for cat in categories if not cat.parent_id]
    tree = [build_tree(cat) for cat in root_categories]
    return jsonify({'success': True, 'categories': tree})

# 获取所有试卷
@app.route('/admin/papers', methods=['GET'])
def get_all_papers():
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': '未登录'})
    papers = Paper.query.all()
    result = []
    for paper in papers:
        # 处理没有分类的情况
        category_name = paper.category.name if paper.category else '未分类'
        result.append({
            'id': paper.id,
            'title': paper.title,
            'category_id': paper.category_id,
            'category_name': category_name,
            'upload_time': paper.upload_time.isoformat(),
            'download_count': paper.download_count
        })
    return jsonify({'success': True, 'papers': result})

# 删除试卷
@app.route('/admin/paper/delete/<int:paper_id>', methods=['DELETE'])
def delete_paper(paper_id):
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': '未登录'})
    paper = Paper.query.get(paper_id)
    if paper:
        try:
            # 先删除关联的下载记录
            DownloadRecord.query.filter_by(paper_id=paper_id).delete()
            # 再删除关联的购买记录
            PaperPurchase.query.filter_by(paper_id=paper_id).delete()
            # 删除关联的文件
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], paper.file_path)
            if os.path.exists(file_path):
                os.remove(file_path)
            # 最后删除试卷记录
            db.session.delete(paper)
            db.session.commit()
            return jsonify({'success': True})
        except Exception as e:
            db.session.rollback()
            print(f"删除试卷失败: {str(e)}")
            return jsonify({'success': False, 'message': f'删除失败: {str(e)}'})
    return jsonify({'success': False, 'message': '试卷不存在'})

# 修改试卷目录
@app.route('/admin/paper/update-category/<int:paper_id>', methods=['POST'])
def update_paper_category(paper_id):
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': '未登录'})
    data = request.json
    new_category_id = data.get('category_id')
    paper = Paper.query.get(paper_id)
    if paper and new_category_id:
        paper.category_id = new_category_id
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': '试卷或分类不存在'})

# 添加试卷分类
@app.route('/admin/category/add', methods=['POST'])
def add_category():
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': '未登录'})
    data = request.json
    new_category = PaperCategory(
        name=data.get('name'),
        parent_id=data.get('parent_id')
    )
    db.session.add(new_category)
    db.session.commit()
    return jsonify({'success': True, 'message': '目录添加成功'})

# 删除试卷分类
@app.route('/admin/category/delete/<int:category_id>', methods=['DELETE'])
def delete_category(category_id):
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': '未登录'})
    
    # 查找要删除的分类
    category = PaperCategory.query.get(category_id)
    if not category:
        return jsonify({'success': False, 'message': '目录不存在'})
    
    # 处理该分类下的试卷，将它们移至根目录
    Paper.query.filter_by(category_id=category_id).update({'category_id': None})
    
    # 递归删除子分类
    def delete_subcategories(parent_id):
        subcategories = PaperCategory.query.filter_by(parent_id=parent_id).all()
        for subcategory in subcategories:
            # 处理子分类下的试卷
            Paper.query.filter_by(category_id=subcategory.id).update({'category_id': None})
            # 递归删除更深层的子分类
            delete_subcategories(subcategory.id)
            # 删除子分类
            db.session.delete(subcategory)
    
    delete_subcategories(category_id)
    
    # 删除主分类
    db.session.delete(category)
    db.session.commit()
    
    return jsonify({'success': True, 'message': '目录删除成功'})

# 获取所有试卷
@app.route('/client/papers', methods=['GET'])
def get_papers():
    # 获取搜索和过滤参数
    search_query = request.args.get('search', '')
    category_id = request.args.get('category_id', '')
    price_type = request.args.get('price_type', '')
    
    # 构建查询
    query = Paper.query
    
    # 按标题搜索
    if search_query:
        query = query.filter(Paper.title.like(f'%{search_query}%'))
    
    # 按分类过滤
    if category_id:
        query = query.filter_by(category_id=int(category_id))
    
    # 按价格类型过滤
    if price_type:
        query = query.filter_by(price_type=price_type)
    
    papers = query.all()
    result = []
    user_id = session.get('user_id')
    user = User.query.get(user_id) if user_id else None
    is_member = user.is_member and user.member_end > datetime.datetime.now() if user else False
    
    for paper in papers:
        # 计算实际价格
        actual_price = 0.0
        can_download = False
        
        if paper.price_type == 'free_all':
            can_download = True
        elif paper.price_type == 'free_member':
            can_download = is_member
        else:  # paid
            actual_price = paper.original_price
            if is_member:
                actual_price *= 0.8  # 会员打8折
            # 检查用户是否已购买
            if user_id:
                purchase = PaperPurchase.query.filter_by(user_id=user_id, paper_id=paper.id, status='confirmed').first()
                if purchase:
                    can_download = True
        
        result.append({
            'id': paper.id,
            'title': paper.title,
            'category_name': paper.category.name if paper.category else '未分类',
            'upload_time': paper.upload_time.isoformat(),
            'price_type': paper.price_type,
            'original_price': paper.original_price,
            'actual_price': actual_price,
            'is_member': is_member,
            'can_download': can_download
        })
    return jsonify({'success': True, 'papers': result})

# 购买试卷
@app.route('/client/paper/buy/<int:paper_id>', methods=['POST'])
def buy_paper(paper_id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': '未登录'})
    
    paper = Paper.query.get(paper_id)
    if not paper:
        return jsonify({'success': False, 'message': '试卷不存在'})
    
    # 检查价格类型
    if paper.price_type == 'free_all':
        return jsonify({'success': False, 'message': '该试卷全员免费，无需购买'})
    elif paper.price_type == 'free_member':
        user = User.query.get(user_id)
        if user.is_member and user.member_end > datetime.datetime.now():
            return jsonify({'success': False, 'message': '您已是会员，可直接下载'})
        else:
            return jsonify({'success': False, 'message': '该试卷仅对会员免费，请先开通会员'})
    
    # 计算实际价格
    user = User.query.get(user_id)
    is_member = user.is_member and user.member_end > datetime.datetime.now() if user else False
    actual_price = paper.original_price
    if is_member:
        actual_price *= 0.8
    
    # 创建购买请求
    purchase = PaperPurchase(
        user_id=user_id,
        paper_id=paper_id,
        price=actual_price
    )
    db.session.add(purchase)
    db.session.commit()
    
    return jsonify({'success': True, 'request_id': purchase.id, 'price': actual_price})

# 获取用户购买请求
@app.route('/admin/paper/purchases', methods=['GET'])
def get_paper_purchases():
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': '未登录'})
    
    purchases = PaperPurchase.query.filter_by(status='pending').all()
    result = []
    for purchase in purchases:
        result.append({
            'id': purchase.id,
            'username': purchase.user.username,
            'paper_title': purchase.paper.title,
            'price': purchase.price,
            'create_time': purchase.create_time.isoformat()
        })
    return jsonify({'success': True, 'purchases': result})

# 确认试卷购买
@app.route('/admin/paper/purchase/confirm/<int:purchase_id>', methods=['POST'])
def confirm_paper_purchase(purchase_id):
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': '未登录'})
    
    purchase = PaperPurchase.query.get(purchase_id)
    if purchase:
        purchase.status = 'confirmed'
        purchase.confirm_time = datetime.datetime.now()
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': '请求不存在'})

# 获取试卷下载记录
@app.route('/admin/paper/download-records/<int:paper_id>', methods=['GET'])
def get_paper_download_records(paper_id):
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': '未登录'})
    
    # 获取该试卷的所有下载记录
    records = DownloadRecord.query.filter_by(paper_id=paper_id).all()
    result = []
    for record in records:
        result.append({
            'id': record.id,
            'username': record.user.username,
            'email': record.user.email,
            'download_time': record.download_time.isoformat()
        })
    
    return jsonify({'success': True, 'download_records': result})

# 下载试卷
@app.route('/client/paper/download/<int:paper_id>', methods=['GET'])
def download_paper(paper_id):
    # 添加调试日志
    print(f"[DEBUG] Download request - paper_id: {paper_id}")
    print(f"[DEBUG] Request headers: {dict(request.headers)}")
    print(f"[DEBUG] Session data: {dict(session)}")
    print(f"[DEBUG] Request URL: {request.url}")
    print(f"[DEBUG] Request method: {request.method}")
    
    paper = Paper.query.get(paper_id)
    if not paper:
        print(f"[DEBUG] Paper not found with id: {paper_id}")
        return jsonify({'success': False, 'message': '文件不存在'})
    
    print(f"[DEBUG] Paper found - title: {paper.title}, price_type: {paper.price_type}")
    
    # 检查下载权限
    can_download = False
    user_id = session.get('user_id')
    print(f"[DEBUG] user_id from session: {user_id}")
    
    if paper.price_type == 'free_all':
        can_download = True
        print(f"[DEBUG] Paper is free_all, can_download = True")
    elif user_id:
        current_user = User.query.get(user_id)
        if paper.price_type == 'free_member':
            # 检查会员状态
            if current_user.is_member and current_user.member_end > datetime.datetime.now():
                can_download = True
            else:
                print(f"[DEBUG] User is not a valid member")
                return jsonify({'success': False, 'message': '非会员或会员已过期'})
        else:  # paid
            # 检查是否已购买
            purchase = PaperPurchase.query.filter_by(user_id=current_user.id, paper_id=paper.id, status='confirmed').first()
            if purchase:
                can_download = True
            else:
                print(f"[DEBUG] User has not purchased this paper")
                return jsonify({'success': False, 'message': '您尚未购买该试卷'})
    else:
        print(f"[DEBUG] No user_id in session and paper is not free_all")
        return jsonify({'success': False, 'message': '请先登录'})
    
    if can_download:
        # 增加下载次数
        paper.download_count += 1
        
        # 创建下载记录（仅限登录用户）
        if user_id:
            download_record = DownloadRecord(
                user_id=user_id,
                paper_id=paper.id
            )
            db.session.add(download_record)
        
        db.session.commit()
        
        print(f"[DEBUG] Sending file: {paper.file_path}")
        return send_from_directory(app.config['UPLOAD_FOLDER'], paper.file_path, as_attachment=True, download_name=paper.title + '.' + paper.file_path.split('.')[-1])
    
    print(f"[DEBUG] Cannot download this paper")
    return jsonify({'success': False, 'message': '无权下载该试卷'})

# 预览试卷
@app.route('/client/paper/preview/<int:paper_id>', methods=['GET'])
def preview_paper(paper_id):
    paper = Paper.query.get(paper_id)
    if not paper:
        return jsonify({'success': False, 'message': '文件不存在'})
    
    # 检查预览权限
    can_preview = False
    user_id = session.get('user_id')
    
    if paper.price_type == 'free_all':
        can_preview = True
    elif user_id:
        current_user = User.query.get(user_id)
        if paper.price_type == 'free_member':
            if current_user.is_member and current_user.member_end > datetime.datetime.now():
                can_preview = True
        else:  # paid
            purchase = PaperPurchase.query.filter_by(user_id=current_user.id, paper_id=paper.id, status='confirmed').first()
            if purchase:
                can_preview = True
    
    if can_preview:
        return send_from_directory(app.config['UPLOAD_FOLDER'], paper.file_path)
    return jsonify({'success': False, 'message': '无权预览该试卷'})

# 获取支付请求列表
@app.route('/admin/payment/requests', methods=['GET'])
def get_payment_requests():
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': '未登录'})
    requests = PaymentRequest.query.all()
    result = []
    for req in requests:
        result.append({
            'id': req.id,
            'username': req.user.username,
            'member_type': req.member_type,
            'status': req.status,
            'create_time': req.create_time.isoformat()
        })
    return jsonify({'success': True, 'requests': result})

# 确认支付
@app.route('/admin/payment/confirm/<int:request_id>', methods=['POST'])
def confirm_payment(request_id):
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': '未登录'})
    payment_req = PaymentRequest.query.get(request_id)
    if payment_req:
        payment_req.status = 'confirmed'
        user = payment_req.user
        user.is_member = True
        user.member_type = payment_req.member_type
        
        # 设置会员时长
        if payment_req.member_type == '1day':
            user.member_start = datetime.datetime.now()
            user.member_end = datetime.datetime.now() + datetime.timedelta(days=1)
        elif payment_req.member_type == '1month':
            user.member_start = datetime.datetime.now()
            user.member_end = datetime.datetime.now() + datetime.timedelta(days=30)
        elif payment_req.member_type == '12year':
            user.member_start = datetime.datetime.now()
            user.member_end = datetime.datetime.now() + datetime.timedelta(days=4380)
        
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': '请求不存在'})

# 静态文件路由
@app.route('/admin/<path:filename>')
def serve_admin_static(filename):
    return send_from_directory('../frontend/admin', filename)

@app.route('/client/<path:filename>')
def serve_client_static(filename):
    return send_from_directory('../frontend/client', filename)

# 提供上传文件的访问路由
@app.route('/uploads/<path:filename>')
def serve_uploads(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/')
def index():
    return jsonify({'message': 'Exam Management System API'})

# WebSocket 事件处理
@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

# 加入聊天室
@socketio.on('join_room')
def handle_join_room(data):
    room = data['room']
    join_room(room)
    print(f'User joined room: {room}')
    send({'message': '已进入聊天室', 'type': 'system'}, room=room)

# 发送消息
@socketio.on('send_message')
def handle_send_message(data):
    print(f'Received message: {data}')
    message = data['message']
    sender_id = data.get('sender_id', 0)
    sender_type = data['sender_type']
    room_id = data.get('room_id', 0)
    room = data.get('room', f'room_{room_id}')
    
    # 获取发送者姓名
    if sender_type == 'admin':
        sender_name = '管理员'
    else:
        user = User.query.get(sender_id)
        sender_name = user.username if user else '未知用户'
    
    # 保存消息到数据库
    new_message = ChatMessage(
        sender_id=sender_id,
        sender_type=sender_type,
        message=message,
        room_id=room_id
    )
    db.session.add(new_message)
    db.session.commit()
    
    # 发送消息到聊天室
    emit('receive_message', {
        'id': new_message.id,
        'message': message,
        'sender_id': sender_id,
        'sender_name': sender_name,
        'sender_type': sender_type,
        'room_id': room_id,
        'send_time': new_message.send_time.isoformat(),
        'status': new_message.status
    }, room=room)

# 好友管理API

# 查询好友列表
@app.route('/api/friends', methods=['GET'])
@token_required
def get_friends(current_user):
    friends = []
    # 查询用户的好友关系
    relations = FriendRelation.query.filter_by(user_id=current_user.id, status='accepted').all()
    for relation in relations:
        friend = relation.friend
        friends.append({
            'id': friend.id,
            'username': friend.username,
            'email': friend.email
        })
    return jsonify({'success': True, 'friends': friends})

# 添加好友
@app.route('/api/friends/add/<int:friend_id>', methods=['POST'])
@token_required
def add_friend(current_user, friend_id):
    # 检查好友是否存在
    friend = User.query.get(friend_id)
    if not friend:
        return jsonify({'success': False, 'message': '用户不存在'})
    
    # 检查是否已经是好友
    existing_relation = FriendRelation.query.filter(
        db.or_(
            db.and_(FriendRelation.user_id == current_user.id, FriendRelation.friend_id == friend_id),
            db.and_(FriendRelation.user_id == friend_id, FriendRelation.friend_id == current_user.id)
        )
    ).first()
    
    if existing_relation:
        return jsonify({'success': False, 'message': '已经是好友了'})
    
    # 创建好友关系
    new_relation = FriendRelation(
        user_id=current_user.id,
        friend_id=friend_id,
        status='accepted'  # 直接成为好友，无需验证
    )
    db.session.add(new_relation)
    
    # 创建反向关系
    reverse_relation = FriendRelation(
        user_id=friend_id,
        friend_id=current_user.id,
        status='accepted'
    )
    db.session.add(reverse_relation)
    
    db.session.commit()
    return jsonify({'success': True, 'message': '添加好友成功'})

# 删除好友
@app.route('/api/friends/remove/<int:friend_id>', methods=['DELETE'])
@token_required
def remove_friend(current_user, friend_id):
    # 删除正向关系
    FriendRelation.query.filter_by(user_id=current_user.id, friend_id=friend_id).delete()
    # 删除反向关系
    FriendRelation.query.filter_by(user_id=friend_id, friend_id=current_user.id).delete()
    db.session.commit()
    return jsonify({'success': True, 'message': '删除好友成功'})

# 获取聊天历史记录
@app.route('/api/chat/history/<int:room_id>', methods=['GET'])
def get_chat_history(room_id):
    messages = ChatMessage.query.filter_by(room_id=room_id).order_by(ChatMessage.send_time.asc()).all()
    result = []
    for msg in messages:
        # 获取发送者姓名
        if msg.sender_type == 'admin':
            sender_name = '管理员'
        else:
            user = User.query.get(msg.sender_id)
            sender_name = user.username if user else '未知用户'
        
        result.append({
            'id': msg.id,
            'message': msg.message,
            'sender_id': msg.sender_id,
            'sender_name': sender_name,
            'sender_type': msg.sender_type,
            'room_id': msg.room_id,
            'send_time': msg.send_time.isoformat(),
            'status': msg.status
        })
    return jsonify({'success': True, 'messages': result})

# 聊天房间API

# 创建聊天房间
@app.route('/api/rooms', methods=['POST'])
@token_required
def create_room(current_user):
    data = request.json
    name = data.get('name', '新群聊')
    is_group = data.get('is_group', True)
    
    # 创建房间
    new_room = ChatRoom(
        name=name,
        creator_id=current_user.id,
        is_group=is_group
    )
    db.session.add(new_room)
    db.session.commit()
    
    # 添加创建者为房间成员
    new_member = RoomMember(
        room_id=new_room.id,
        user_id=current_user.id
    )
    db.session.add(new_member)
    db.session.commit()
    
    return jsonify({'success': True, 'room': {
        'id': new_room.id,
        'name': new_room.name,
        'is_group': new_room.is_group,
        'created_at': new_room.created_at.isoformat()
    }})

# 获取房间列表
@app.route('/api/rooms', methods=['GET'])
@token_required
def get_rooms(current_user):
    rooms = []
    # 查询用户加入的所有房间
    memberships = RoomMember.query.filter_by(user_id=current_user.id).all()
    for membership in memberships:
        room = membership.room
        rooms.append({
            'id': room.id,
            'name': room.name,
            'is_group': room.is_group,
            'created_at': room.created_at.isoformat()
        })
    return jsonify({'success': True, 'rooms': rooms})

# 加入房间
@app.route('/api/rooms/<int:room_id>/join', methods=['POST'])
@token_required
def join_room_api(current_user, room_id):
    # 检查房间是否存在
    room = ChatRoom.query.get(room_id)
    if not room:
        return jsonify({'success': False, 'message': '房间不存在'})
    
    # 检查是否已经是房间成员
    existing_member = RoomMember.query.filter_by(room_id=room_id, user_id=current_user.id).first()
    if existing_member:
        return jsonify({'success': False, 'message': '已经是房间成员了'})
    
    # 添加为房间成员
    new_member = RoomMember(
        room_id=room_id,
        user_id=current_user.id
    )
    db.session.add(new_member)
    db.session.commit()
    
    return jsonify({'success': True, 'message': '加入房间成功'})

# 离开房间
@app.route('/api/rooms/<int:room_id>/leave', methods=['POST'])
@token_required
def leave_room_api(current_user, room_id):
    # 检查房间是否存在
    room = ChatRoom.query.get(room_id)
    if not room:
        return jsonify({'success': False, 'message': '房间不存在'})
    
    # 删除房间成员关系
    RoomMember.query.filter_by(room_id=room_id, user_id=current_user.id).delete()
    db.session.commit()
    
    return jsonify({'success': True, 'message': '离开房间成功'})

# 标记消息为已读
@app.route('/api/chat/read/<int:message_id>', methods=['POST'])
def mark_message_as_read(message_id):
    message = ChatMessage.query.get(message_id)
    if message:
        message.status = 'read'
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': '消息不存在'})

if __name__ == '__main__':
    # 使用 socketio.run 替代 app.run
    socketio.run(app, debug=False, host='0.0.0.0', port=5000)
