from src.utils.utils_train import CustomSchedule
import tensorflow as tf
import os
from src.models.Baselines.Transformer_without_enc import Transformer
from src.train.train_functions import train_baseline_transformer
from src.models.SMC_Transformer.transformer_utils import create_look_ahead_mask
from src.algos.run_rnn import Algo
from src.utils.utils_train import restoring_checkpoint
import datetime
import numpy as np


class BaselineTAlgo(Algo):
    def __init__(self, dataset, args):
        super(BaselineTAlgo, self).__init__(dataset=dataset, args=args)
        self.lr = CustomSchedule(args.d_model)
        self.optimizer = tf.keras.optimizers.Adam(self.lr,
                                                  beta_1=0.9,
                                                  beta_2=0.98,
                                                  epsilon=1e-9)
        self.out_folder = self._create_out_folder(args=args)
        self.logger = self.create_logger()
        self.ckpt_path = self.create_ckpt_path()
        self.save_hparams(args)
        self.train_dataset, self.val_dataset, self.test_dataset = self.load_datasets(num_dim=3)
        self.transformer = Transformer(num_layers=1, d_model=args.d_model, num_heads=1, dff=args.dff,
                                       target_vocab_size=self.output_size,
                                       maximum_position_encoding=args.pe, rate=args.p_drop, full_model=args.full_model)
        self.ckpt_manager, _ = self._load_ckpt()

    def _MC_Dropout(self, inp_model, save_path=None):
        '''
        :param LSTM_hparams: shape_input_1, shape_input_2, shape_ouput, num_units, dropout_rate
        :param inp_model: array of shape (B,S,F)
        :param mc_samples:
        :return:
        '''
        list_predictions = []
        seq_len = tf.shape(inp_model)[-2]
        for i in range(self.mc_samples):
            predictions_test, _ = self.transformer(inputs=inp_model,
                                                   training=True,
                                                   mask=create_look_ahead_mask(seq_len))  # (B,S,1)
            list_predictions.append(predictions_test)
        predictions_test_MC_Dropout = tf.squeeze(tf.stack(list_predictions, axis=1))  # shape (B, N, S)
        print('MC Dropout unistep done')
        if save_path is not None:
            np.save(save_path, predictions_test_MC_Dropout)
        return predictions_test_MC_Dropout

    def _MC_Dropout_multistep(self, inp_model, len_future=None, save_path=None):
        '''
            :param LSTM_hparams: shape_input_1, shape_input_2, shape_ouput, num_units, dropout_rate
            :param inp_model: array of shape (B,S,F)
            :param mc_samples:
            :return:
        '''
        if len_future is None:
            len_future = self.seq_len - self.past_len
        list_predictions = []
        for i in range(self.mc_samples):
            inp = inp_model
            for t in range(len_future + 1):
                seq_len = tf.shape(inp)[-2]
                preds_test, _ = self.transformer(inputs=inp,
                                                 training=True,
                                                 mask=create_look_ahead_mask(seq_len))  # (B,S,1)
                last_pred = tf.expand_dims(preds_test[:, -1, :], axis=-2)
                inp = tf.concat([inp, last_pred], axis=1)
            list_predictions.append(preds_test)
        preds_test_MC_Dropout = tf.squeeze(tf.stack(list_predictions, axis=1))  # (B,S,N)
        print('mc dropout multistep done')
        if save_path is not None:
            np.save(save_path, preds_test_MC_Dropout)
        return preds_test_MC_Dropout

    def launch_inference(self, **kwargs):
        mc_dropout_unistep_path, mc_dropout_multistep_path = self._get_inference_paths()
        for (inp, _) in self.test_dataset:
            mc_samples_uni = self._MC_Dropout(inp_model=inp, save_path=mc_dropout_unistep_path)
            print("mc dropout samples unistep shape", mc_samples_uni.shape)
            if kwargs["multistep"]:
                mc_samples_multi = self._MC_Dropout_multistep(inp_model=inp[:, :self.past_len, :],
                                                              save_path=mc_dropout_multistep_path)
                print("mc dropout samples multistep shape", mc_samples_multi.shape)

    def _create_out_folder(self, args):
        out_file = '{}_Classic_T_depth_{}_dff_{}_pe_{}_bs_{}_fullmodel_{}'.format(args.dataset, args.d_model, args.dff,
                                                                                  args.pe,
                                                                                  self.bs, args.full_model)
        datetime_folder = "{}".format(datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        output_folder = os.path.join(args.output_path, out_file, datetime_folder)
        if not os.path.isdir(output_folder):
            os.makedirs(output_folder)
        return output_folder

    def _load_ckpt(self, num_train=1):
        # creating checkpoint manager
        ckpt = tf.train.Checkpoint(transformer=self.transformer,
                                   optimizer=self.optimizer)
        baseline_T_ckpt_path = os.path.join(self.ckpt_path, "baseline_transformer_{}".format(num_train))
        ckpt_manager = tf.train.CheckpointManager(ckpt, baseline_T_ckpt_path, max_to_keep=50)
        # if a checkpoint exists, restore the latest checkpoint.
        start_epoch = restoring_checkpoint(ckpt_manager=ckpt_manager, ckpt=ckpt, args_load_ckpt=True,
                                           logger=self.logger)
        if start_epoch is not None:
            self.start_epoch = start_epoch
        else:
            start_epoch = 0
        return ckpt_manager, start_epoch

    def train(self):
        self.logger.info('hparams...')
        self.logger.info(
            'd_model:{} - dff:{} - positional encoding: {} - learning rate: {}'.format(self.transformer.decoder.d_model,
                                                                                       self.transformer.decoder.dff,
                                                                                       self.transformer.decoder.maximum_position_encoding,
                                                                                       self.lr))
        self.logger.info('Transformer with one head and one layer')
        if not self.cv:
            train_baseline_transformer(transformer=self.transformer,
                                       optimizer=self.optimizer,
                                       EPOCHS=self.EPOCHS,
                                       train_dataset=self.train_dataset,
                                       val_dataset=self.val_dataset,
                                       output_path=self.out_folder,
                                       ckpt_manager=self.ckpt_manager,
                                       logger=self.logger,
                                       start_epoch=self.start_epoch,
                                       num_train=1)
            self.logger.info("training for a Baseline Transformer done...")
            self.logger.info('-' * 60)
        else:
            for num_train, (train_dataset, val_dataset) in enumerate(zip(self.train_dataset, self.val_dataset)):
                ckpt_manager, start_epoch = self._load_ckpt(num_train=num_train + 1)
                train_baseline_transformer(transformer=self.transformer,
                                           optimizer=self.optimizer,
                                           EPOCHS=self.EPOCHS,
                                           train_dataset=train_dataset,
                                           val_dataset=val_dataset,
                                           output_path=self.out_folder,
                                           ckpt_manager=ckpt_manager,
                                           logger=self.logger,
                                           start_epoch=start_epoch,
                                           num_train=num_train + 1)
                self.logger.info(
                    "training of a Baseline Transformer for train/val split number {} done...".format(num_train + 1))
                self.logger.info('-' * 60)

    def test(self):
        for (inp, tar) in self.test_dataset:
            seq_len = tf.shape(inp)[-2]
            predictions_test, _ = self.transformer(inputs=inp,
                                                   training=False,
                                                   mask=create_look_ahead_mask(seq_len))  # (B,S,F)
            loss_test = tf.keras.losses.MSE(tar, predictions_test)  # (B,S)
            loss_test = tf.reduce_mean(loss_test)
        self.logger.info("test loss at the end of training: {}".format(loss_test))
