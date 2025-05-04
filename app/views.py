from flask import request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from app import app
import os
import joblib
import pandas as pd
import oracledb
load_dotenv('.env')
oracledb.defaults.force_thin = True 

modelo = joblib.load('app/modelo_tempo_atraso.pkl')



CORS(app)  

def conectar_oracle():
    conexao = oracledb.connect(
        user= os.getenv('USUARIO_ORACLE'),
        password=os.getenv('SENHA_ORACLE'),
        host= os.getenv('HOST_ORACLE'),
        port=1521,
        sid="orcl"  
    )
    return conexao



@app.route('/')
def home():
    return "API Ativa!"


#Recolher dados de uma estação no banco
@app.route('/dados_estacao', methods=['GET'])
def dados_estacao():
    nome_estacao = request.args.get('estacao')

    try:
        conn = conectar_oracle()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT *
            FROM T_TTCCR_ANALISE_PRED 
            WHERE ESTACAO = :1
        """, [nome_estacao])

        colunas = [desc[0] for desc in cursor.description]
        dados = [dict(zip(colunas, row)) for row in cursor.fetchall()]

        cursor.close()
        conn.close()

        return jsonify(dados)
    
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


#Serviço de predição de atraso de um trem
@app.route('/prever', methods=['POST'])
def prever():
    dados = request.json

    try:
        
        entrada = pd.DataFrame([dados])

       
        predicao = modelo.predict(entrada)

        return jsonify({
            'previsao_atraso': round(float(predicao[0]), 2)
        })

    except Exception as e:
        return jsonify({'erro': str(e)}), 400


#Postar report feito por usuario logado ou anonimo no banco
@app.route('/reports', methods=['POST'])
def criar_report():

   

    conn= conectar_oracle() 
    cursor = conn.cursor()
    data = request.json

    # Validação modificada
    campos_obrigatorios = ['tipo_alerta', 'descricao_alerta', 'estacao']
    if not data.get('id_usuario'):
        campos_obrigatorios.extend(['nome_anonimo', 'email_anonimo'])

    if not all(data.get(campo) for campo in campos_obrigatorios):
        return jsonify({'erro': f'Campos obrigatórios: {", ".join(campos_obrigatorios)}'}), 400

    # Verificar usuário apenas se estiver logado
    if data.get('id_usuario'):
        cursor.execute("SELECT COUNT(*) FROM T_TTCCR_USUARIO WHERE ID_USUARIO = :id", {'id': data['id_usuario']})
        if cursor.fetchone()[0] == 0:
            return jsonify({'erro': f"ID de usuário {data['id_usuario']} não encontrado"}), 404

    try:
        cursor.execute("SELECT NVL(MAX(ID_REPORT), 0) FROM T_TTCCR_REPORT_USUARIO")
        id_report = cursor.fetchone()[0] + 1

        query = """
            INSERT INTO T_TTCCR_REPORT_USUARIO
            (ID_REPORT, ID_USUARIO, TIPO_ALERTA, DESCRICAO_ALERTA, DATA_REPORT, NOME_ANONIMO, EMAIL_ANONIMO, ESTACAO)
            VALUES (:1, :2, :3, :4, CURRENT_TIMESTAMP, :5, :6, :7)
        """
        params = (
            id_report,
            data.get('id_usuario'),  
            data['tipo_alerta'],
            data['descricao_alerta'],
            data.get('nome_anonimo'),
            data.get('email_anonimo'),
            data.get('estacao')
        )

        cursor.execute(query, params)
        conn.commit()
        
        return jsonify({
            'mensagem': 'Report criado com sucesso',
            'id_report': id_report
        }), 201

    except Exception as e:
        conn.rollback()
        return jsonify({'erro': f"Erro ao inserir report: {str(e)}"}), 500
    

#Recolher reports feitos por usuario logado
@app.route('/reports', methods=['GET'])
def listar_reports_usuario():
    conn, cursor = None, None
    try:
        conn = conectar_oracle()
        cursor = conn.cursor()
        id_usuario = request.headers.get('X-User-ID')
        
        if not id_usuario:
            return jsonify({'erro': 'Acesso não autorizado'}), 401

        
        query = """
            SELECT 
                r.id_report,
                r.tipo_alerta,
                TO_CHAR(r.descricao_alerta) as descricao_alerta,
                TO_CHAR(r.data_report, 'YYYY-MM-DD HH24:MI:SS') as data_report,
                r.estacao
            FROM t_ttccr_report_usuario r
            JOIN t_ttccr_usuario u ON r.id_usuario = u.id_usuario
            WHERE r.id_usuario = :id
            ORDER BY r.data_report DESC
        """
        
        cursor.execute(query, {'id': id_usuario})
        
        # Processamento dos resultados
        colunas = [col[0].lower() for col in cursor.description]
        reports = [dict(zip(colunas, row)) for row in cursor]
        
        return jsonify(reports), 200

    except oracledb.DatabaseError as e:
        error, = e.args
        print(f"Oracle Error: {error.code} - {error.message}") 
        return jsonify({
            'erro': 'Erro no banco de dados',
            'detalhes': f"Oracle {error.code}: {error.message}"
        }), 500
        
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return jsonify({'erro': 'Falha no servidor'}), 500
        
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


#modificação de report para usuario logado
@app.route('/reports/<int:id_report>', methods=['PUT'])
def atualizar_report(id_report):
    conn, cursor = None, None
    try:
        conn = conectar_oracle()
        cursor = conn.cursor()
        
        # 1. Obter dados do corpo da requisição CORRETAMENTE
        dados = request.get_json()  # Correto

        if not dados:
            return jsonify({'erro': 'Dados JSON ausentes'}), 400
            
        nova_descricao = dados.get('nova_descricao')

       
        if not nova_descricao:
            return jsonify({'erro': 'Campo "nova_descricao" obrigatório'}), 400
        
        # 2. Obter ID do usuário do header
        id_usuario = request.headers.get('X-User-ID')
        if not id_usuario:
            return jsonify({'erro': 'Acesso não autorizado'}), 401
        
        # 3. Executar a atualização
        cursor.execute("""
            UPDATE t_ttccr_report_usuario
            SET descricao_alerta = :descricao
            WHERE id_report = :id_report
            AND id_usuario = :id_usuario
        """, {
            'descricao': nova_descricao,
            'id_report': id_report,
            'id_usuario': id_usuario
        })
        
        if cursor.rowcount == 0:
            return jsonify({'erro': 'Report não encontrado ou não pertence ao usuário'}), 404
            
        conn.commit()
        return jsonify({'mensagem': 'Descrição atualizada com sucesso'}), 200
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"Erro: {str(e)}")
        return jsonify({'erro': 'Falha na atualização', 'detalhes': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()



#Deleção de report para usuario logado
@app.route('/reports/<int:id_report>', methods=['DELETE'])
def deletar_report(id_report):
    conn= conectar_oracle()
    cursor = conn.cursor()

   
    id_usuario = request.headers.get('X-User-ID')

    try:
        cursor.execute("""
            DELETE FROM t_ttccr_report_usuario
            WHERE id_report = :id_report AND id_usuario = :id_usuario
        """, {'id_report': id_report, 'id_usuario': id_usuario})
        
        if cursor.rowcount == 0:
            return jsonify({'erro': 'Report não encontrado ou não autorizado'}), 404
        
        conn.commit()
        return jsonify({'mensagem': 'Report deletado com sucesso'}), 200
    
    except Exception as e:
        conn.rollback()
        return jsonify({'erro': str(e)}), 500